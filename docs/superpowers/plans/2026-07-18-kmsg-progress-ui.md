# kmsg / Progress UI Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the kmsg color scheme with a glyph + severity-color format, add a live spinner + overall progress bar to the slow paths, fix the polling-loop bugs in `kopsrox_proxmox.py`, and make errors exit 1 instead of 0.

**Architecture:** `lib/kopsrox_kmsg.py` becomes the single output module (raw ANSI, no deps): `kmsg` keeps its signature, `kabort` replaces the ~40 `kmsg err + exit(0)` pairs, `kstep` is a context manager owning one live spinner line animated by a daemon thread, `kplan`/`kplan_tick` drive a `4/9` bar for compound verbs. Wait sites in `kopsrox_proxmox.py`/`kopsrox_k3s.py` get wrapped in `kstep` and their real bugs fixed (tight loop, ignored `node` arg, UnboundLocalError, missing timeout). Verbs compute step totals upfront. Import-side-effect architecture unchanged.

**Tech Stack:** Python 3 stdlib only (`threading`, `atexit`, ANSI escapes). termcolor dependency dropped.

## Global Constraints

- 2-space indentation, lowercase informal comments — match existing file style exactly.
- `kmsg(kname, msg, sev)` signature must not change; all 97 call sites keep working.
- kname format stays `scope_action` split on the FIRST `_`.
- Errors exit 1 (via `kabort`); success paths keep `exit(0)`. No other behavior changes: same commands, same ordering — presentation + polling hygiene + exit codes only.
- Non-tty stdout (or `NO_COLOR` for color): no ANSI, no animation; non-quiet steps print a start line and a done line; quiet steps print nothing.
- No new Python dependencies. No requirements.txt exists; deps are listed in CLAUDE.md.
- Never commit as Claude — plain commit messages in repo style (short, lowercase), no Co-Authored-By.
- `./kopsrox.py cluster info` touches the live Proxmox host (config import powers on stopped VMs) — approved as verification in the design session.
- `lib/kopsrox_kmsg.py` must import nothing from other kopsrox modules (kopsrox_config imports it).

---

### Task 1: Rewrite `lib/kopsrox_kmsg.py` + standalone test

**Files:**
- Rewrite: `lib/kopsrox_kmsg.py`
- Create: `dev/test_kmsg.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (used by every later task):
  - `kmsg(kname='kopsrox', msg='no msg', sev='info')` — severity line. sev in `info|sys|err|done`.
  - `kabort(kname, msg)` — err line then `exit(1)`. Never returns.
  - `kstep(kname, msg, quiet=False)` — context manager. `.msg` attribute is settable mid-flight. Non-quiet: ✓ line with elapsed on clean exit. quiet=True: nothing printed on success (polling internals).
  - `kplan(add, title=None)` — start or extend the overall plan (bar shows `done/total`).
  - `kplan_tick()` — one plan unit done. No-op when no plan active.

- [ ] **Step 1: Write the failing test**

Create `dev/test_kmsg.py`:

```python
#!/usr/bin/env python3

# checks for lib/kopsrox_kmsg.py
# scripted: runs itself as a child with piped ( non tty ) stdout and asserts on plain output
# visual: run in a terminal for a live spinner / bar / color demo after the asserts pass

import sys, os, time, subprocess

# child mode - emit through the real module with piped stdout
if os.environ.get('KMSG_CHILD'):
  sys.path[0:0] = ['lib/']
  from kopsrox_kmsg import kmsg, kabort, kstep, kplan, kplan_tick
  kmsg('test_info', 'plain info')
  kmsg('test_sys', 'warning line', 'sys')
  kmsg('test_err', 'error line', 'err')
  kmsg('test_multi', 'first\nsecond')
  kplan(2, 'test plan')
  with kstep('test_step', 'visible step'):
    time.sleep(0.2)
  kplan_tick()
  with kstep('test_quiet', 'invisible step', quiet = True) as step:
    step.msg = 'updated'
    time.sleep(0.1)
  kplan_tick()
  if sys.argv[1:] == ['abort']:
    kabort('test_abort', 'aborting')
  exit(0)

env = dict(os.environ, KMSG_CHILD = '1')

# clean run
run = subprocess.run([sys.executable, __file__], env = env, capture_output = True, text = True)
out = run.stdout
assert run.returncode == 0, f'expected exit 0 got {run.returncode}\n{out}{run.stderr}'
assert '\x1b[' not in out, 'ansi codes leaked into non-tty output'
assert '· test:info' in out, out
assert '! test:sys' in out, out
assert '✗ test:err' in out, out
assert '\n  second' in out, 'multiline continuation not indented: ' + out
assert '· test:step' in out, 'non-quiet step missing start line: ' + out
assert '✓ test:step' in out and 'visible step (' in out, 'non-quiet step missing done line: ' + out
assert 'test:quiet' not in out, 'quiet step printed in non-tty: ' + out

# abort run
run = subprocess.run([sys.executable, __file__, 'abort'], env = env, capture_output = True, text = True)
assert run.returncode == 1, f'kabort should exit 1 - got {run.returncode}'
assert '✗ test:abort' in run.stdout, run.stdout

print('kmsg tests OK')

# visual demo when run interactively
if sys.stdout.isatty():
  sys.path[0:0] = ['lib/']
  from kopsrox_kmsg import kmsg, kstep, kplan, kplan_tick
  kplan(3, 'demo plan')
  for n in range(3):
    with kstep('demo_step', f'step {n + 1} of 3') as step:
      time.sleep(1)
      step.msg = f'step {n + 1} of 3 - nearly there'
      time.sleep(1)
    kplan_tick()
  kmsg('demo_done', 'demo finished', 'sys')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/simonc/GIT/anchovy && chmod +x dev/test_kmsg.py && ./dev/test_kmsg.py | cat`
Expected: AssertionError (child exits non-zero / output mismatch) — old module has no `kabort`/`kstep`/`kplan`, so the child dies with ImportError.

- [ ] **Step 3: Rewrite the module**

Replace the entire content of `lib/kopsrox_kmsg.py`:

```python
#!/usr/bin/env python3

# kopsrox output module - the only module that emits ansi
# kmsg() severity lines, kabort() error + exit 1, kstep() live spinner steps,
# kplan()/kplan_tick() overall progress bar for compound verbs
# degrades to plain sequential lines when stdout is not a tty ( NO_COLOR kills color )

import sys, os, time, threading, atexit

# ansi bits
RESET = '\x1b[0m'
BOLD = '\x1b[1m'
COLOR = {
  'green':  '\x1b[32m',
  'yellow': '\x1b[33m',
  'red':    '\x1b[31m',
  'cyan':   '\x1b[36m',
  'blue':   '\x1b[34m',
}

# severity -> glyph, color, bold
SEV = {
  'info': ('·', 'cyan',   False),
  'sys':  ('!', 'yellow', True),
  'err':  ('✗', 'red',    True),
  'done': ('✓', 'green',  False),
}

SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
KNAME_PAD = 22
BAR_WIDTH = 12

# animation needs a tty - color additionally honours NO_COLOR
TTY = sys.stdout.isatty()
COLOR_ON = TTY and not os.environ.get('NO_COLOR')

# shared state - guarded by LOCK
LOCK = threading.RLock()
STEPS = []      # stack of live ksteps - innermost last
PLAN = None     # {'title': str, 'total': int, 'done': int}
LIVE = 0        # lines the live region currently occupies
THREAD = None   # render thread

def paint(text, color, bold = False):
  if not COLOR_ON:
    return text
  return (BOLD if bold else '') + COLOR[color] + text + RESET

# scope_action -> scope:action - split on the first _
def fmt_kname(kname):
  scope, _, action = kname.partition('_')
  return f'{scope}:{action}' if action else scope

def fmt_secs(secs):
  if secs < 60:
    return f'{secs:.1f}s'
  return f'{int(secs // 60)}m{int(secs % 60):02d}s'

# render one message - pad computed on plain text before color is added
def fmt_line(sev, kname, msg):
  glyph, color, bold = SEV[sev]
  name = fmt_kname(kname)
  pad = ' ' * max(1, KNAME_PAD - len(name) - 2)
  head = paint(glyph, color, bold) + ' ' + paint(name, color, bold) + pad
  lines = str(msg).split('\n')
  out = head + lines[0]
  for line in lines[1:]:
    out += '\n  ' + line
  return out

# wipe the live region - cursor ends at column 0 of its first line
def clear_live():
  global LIVE
  if LIVE:
    sys.stdout.write('\r' + (f'\x1b[{LIVE - 1}A' if LIVE > 1 else '') + '\x1b[0J')
    LIVE = 0

# draw the live region - plan bar line then innermost step spinner line
def draw_live():
  global LIVE
  lines = []
  if PLAN:
    done = min(PLAN['done'], PLAN['total'])
    fill = round(BAR_WIDTH * done / PLAN['total']) if PLAN['total'] else BAR_WIDTH
    bar = paint('█' * fill, 'green') + paint('░' * (BAR_WIDTH - fill), 'cyan')
    lines.append(f"{paint('kopsrox', 'blue', True)} {PLAN['title']} {paint('──', 'cyan')} {bar} {done}/{PLAN['total']}")
  if STEPS:
    step = STEPS[-1]
    frame = SPINNER[int(time.monotonic() * 10) % len(SPINNER)]
    name = fmt_kname(step.kname)
    pad = ' ' * max(1, KNAME_PAD - len(name) - 2)
    elapsed = paint(f'({fmt_secs(time.monotonic() - step.t0)})', 'cyan')
    lines.append(f"{paint(frame, 'cyan', True)} {paint(name, 'cyan')}{pad}{step.msg} {elapsed}")
  if lines:
    sys.stdout.write('\n'.join(lines))
    LIVE = len(lines)
  sys.stdout.flush()

# print a permanent line without tearing the live region
def emit(text):
  with LOCK:
    if TTY:
      clear_live()
    sys.stdout.write(text + '\n')
    if TTY:
      draw_live()
    sys.stdout.flush()

# render thread - animates spinner + elapsed while anything is live
def render_loop():
  while True:
    time.sleep(0.1)
    with LOCK:
      if STEPS or PLAN:
        clear_live()
        draw_live()

def ensure_thread():
  global THREAD
  if TTY and THREAD is None:
    sys.stdout.write('\x1b[?25l')
    THREAD = threading.Thread(target = render_loop, daemon = True)
    THREAD.start()

# wipe live output and restore the cursor on exit
@atexit.register
def cleanup():
  with LOCK:
    STEPS.clear()
    if TTY:
      clear_live()
      if THREAD:
        sys.stdout.write('\x1b[?25h')
      sys.stdout.flush()

# kmsg - severity message line - same signature as always
def kmsg(kname = 'kopsrox', msg = 'no msg', sev = 'info'):
  emit(fmt_line(sev, kname, msg))

# kabort - error message then exit non zero
def kabort(kname, msg):
  with LOCK:
    # live steps are dead - stop the render thread redrawing them
    STEPS.clear()
    kmsg(kname, msg, 'err')
  exit(1)

# kplan - start or extend the overall step plan
def kplan(add, title = None):
  global PLAN
  with LOCK:
    if PLAN is None:
      PLAN = {'title': title or '', 'total': 0, 'done': 0}
    if title:
      PLAN['title'] = title
    PLAN['total'] += add

# kplan_tick - one plan unit done
def kplan_tick():
  with LOCK:
    if PLAN:
      PLAN['done'] += 1

# kstep - live spinner line while a slow operation runs
# quiet steps never print on success - for polling internals like qa_exec
class kstep:

  def __init__(self, kname, msg, quiet = False):
    self.kname = kname
    self.msg = msg
    self.quiet = quiet
    self.t0 = time.monotonic()

  def __enter__(self):
    with LOCK:
      STEPS.append(self)
      # non tty gets a plain start line instead of the spinner
      if not TTY and not self.quiet:
        sys.stdout.write(fmt_line('info', self.kname, self.msg) + '\n')
        sys.stdout.flush()
      ensure_thread()
    return self

  def __exit__(self, exc_type, exc, tb):
    with LOCK:
      if self in STEPS:
        STEPS.remove(self)
      if exc_type is None and not self.quiet:
        emit(fmt_line('done', self.kname, f'{self.msg} ({fmt_secs(time.monotonic() - self.t0)})'))
      elif TTY:
        clear_live()
        draw_live()
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/simonc/GIT/anchovy && ./dev/test_kmsg.py | cat`
Expected: `kmsg tests OK`

Then run `./dev/test_kmsg.py` directly in the terminal — expect the asserts to pass and a ~6s visual demo: bar line + spinner animating, three ✓ lines, no torn output, cursor restored.

- [ ] **Step 5: Commit**

```bash
git add lib/kopsrox_kmsg.py dev/test_kmsg.py
git commit -m "kopsrox_kmsg: glyph/severity output, kabort, kstep spinner, kplan progress bar - drop termcolor"
```

---

### Task 2: `lib/kopsrox_config.py` — kabort sweep + control-flow cleanup

**Files:**
- Modify: `lib/kopsrox_config.py`

**Interfaces:**
- Consumes: `kmsg, kabort, kstep, kplan, kplan_tick` from Task 1.
- Produces: the widened import makes `kabort`/`kstep`/`kplan`/`kplan_tick` available to every downstream module via the existing `from kopsrox_config import *` chain. `conf_check_master_up` (already exists) is used by Task 4's `cluster_plan_total()`.

All edits below preserve behavior except exit code (now 1) and two latent crashes fixed (undefined `disc_storages`, undefined `prox` in the API-failure print).

- [ ] **Step 1: Widen the kmsg import (line 12)**

```python
# kmsg
from kopsrox_kmsg import kmsg, kabort, kstep, kplan, kplan_tick
```

- [ ] **Step 2: Rewrite `conf_check` (lines 25-64)** — the current version signals "missing option" by raising SystemExit inside its own try block; make it direct:

```python
# check section and value exists in kopsrox.ini
def conf_check(value: str = 'kopsrox'):

  # check option exists
  if not kopsrox_config.has_option('kopsrox', value):
    kabort(kname, f'{value} is missing in kopsrox.ini')

  # define config_item
  config_item = kopsrox_config.get('kopsrox', value)

  # check value is not blank - some values may be
  if config_item == '' and value not in ['extra_packages', 's3_region']:
    kabort(kname, f'{value} - a value is required')

  # int check
  if value in ['proxmox_api_port', 'vm_cpu', 'vm_ram', 'vm_disk', 'cluster_id', 'workers', 'masters', 'network_mtu']:
    try:
      return int(config_item)
    except:
      kabort(kname, f'{value} should be numeric: {config_item}')

  # return string
  return str(config_item)
```

- [ ] **Step 3: Convert every `kmsg(..., 'err')` + `exit(0)` pair to `kabort`**

Apply each of these (old → new):

- cluster_id too low (l.72-74): `kabort(kname, f'cluster_id is too low - should be over 100')`
- endpoint localhost (l.81-83): `kabort(kname, f'proxmox_endpoint cannot be localhost - please use a reachable IP')`
- API connection failure (l.105-108) — also fixes the undefined-`prox` re-print:

```python
except Exception as e:
  kabort(kname, f'API connection to proxmox failed check proxmox settings\n{e}')
```

- node not found (l.113-115): `kabort(kname, f'"{proxmox_node}" not found - discovered nodes: {disc_nodes}')`
- k3s down warning (l.126): continues execution, so it is a warning not an error — change sev only: `kmsg(kname, f'k3s down but master server available...?', 'sys')`
- storage not found (l.132-134) — `disc_storages` was never defined, drop the print:

```python
if not prox.nodes(proxmox_node).storage.get(storage = proxmox_storage):
  kabort(kname, f'{proxmox_storage} storage not found')
```

- pve-microvm not installed (l.145-147): `kabort(kname, 'pve-microvm is not installed - see docs/SETUP.md')`
- pve-microvm too old (l.149-151): `kabort(kname, f'pve-microvm {microvm_ver} is too old - kopsrox needs 0.3.19 or later')`
- vm_disk (l.174-176): `kabort(kname, f'vm_ - kopsrox vms need 20G disk')`
- vm_cpu (l.180-182): `kabort(kname, f'vm_ - kopsrox vms at least 1 cpu')`
- vm_ram (l.186-188): `kabort(kname, f'vm_ram - kopsrox vms need 2G RAM')`
- ssh key (l.194-196): `kabort(kname, f'[kopsrox]/cloudinitsshkey - invalid ssh key')`
- masters check (l.216-218) — currently missing the err sev entirely: `kabort(kname, f'[cluster] - masters: only 1 or 3 masters supported. You have: {masters}')`
- sdn parse failure (l.290-298): keep the structure, `exit(0)` on missing params and the except both become kabort:

```python
    try:
      sdn_params = network_bridge.split('/')
      zone = sdn_params[1]
      network_bridge = sdn_params[2]
    except:
      kabort(kname, f'unable to parse sdn config: "{network_bridge}"')
```

- bridge not in list (l.304-306): `kabort(kname, f'"{network_bridge}" not found. valid bridges: {discovered_bridges}')`

- [ ] **Step 4: Rewrite the image-exists check (lines 308-327)** — another SystemExit-as-control-flow block:

```python
# check the image exists - image create builds it so skips the check
if not (sys.argv[1] == 'image' and sys.argv[2:3] == ['create']):
  try:
    img_found = kopsrox_img()
  except:
    img_found = False
  if not img_found:
    kabort(kname, f'{cluster_name} image not found - please run "kopsrox image create"')
```

- [ ] **Step 5: Clean `get_k3s_token` and `local_exec`**

`get_k3s_token` (l.346-350): delete the unreachable `exit(0)` after `return`.

`local_exec` (l.360-370) — same error condition (rc == 1 or any stderr), direct abort with the failing command:

```python
# run local os process
def local_exec(cmd):
  cmd_run = subprocess.run(['bash', '-c', cmd], text = True, capture_output = True)

  # if return code 1 or any stderr
  if cmd_run.returncode == 1 or cmd_run.stderr != '':
    kabort('local_exec-process-error', f'{cmd}\n{cmd_run.stderr.strip()}')
  return cmd_run
```

- [ ] **Step 6: Verify**

Run: `python3 -m py_compile lib/kopsrox_config.py && ./kopsrox.py && ./kopsrox.py cluster info | head -20`
Expected: compile clean; bare run prints usage; `cluster info` renders new-format `·`/`!` lines and the node table (live cluster) or a red `✗ ... image/cluster not found` exiting 1 (`echo $?` → 1 on error paths).

- [ ] **Step 7: Commit**

```bash
git add lib/kopsrox_config.py
git commit -m "kopsrox_config: kabort error paths ( exit 1 ), fix broken error prints, straighten conf_check/image-check flow"
```

---

### Task 3: `lib/kopsrox_proxmox.py` — kstep wrappers + polling fixes

**Files:**
- Modify: `lib/kopsrox_proxmox.py`

**Interfaces:**
- Consumes: `kstep`, `kabort`, `kplan_tick` (via `from kopsrox_config import *`).
- Produces: `qa_exec(vmid, cmd, node, timeout=600)` (new timeout param, same return contract: stripped stdout or `'no output-'+cmd`), `prox_task(task_id, node=proxmox_node, timeout=600)`, `clone(vmid)` now calls `kplan_tick()` once at its end (clone+prepare = one plan unit). Everything else keeps its signature.

Real fixes bundled here: `prox_task` tight loop (no sleep) + ignored `node` arg; `qa_exec` `print(qa_exec)` UnboundLocalError, bare-except pyramid on `out-data`/`err-data`, missing exec-status timeout (the `# fixme`), inconsistent node targeting between ping/exec/exec-status; `task_log` unreachable return + ignored `node`.

- [ ] **Step 1: Rewrite `qa_exec` (lines 7-106)**

```python
# run a exec via qemu-agent
def qa_exec(vmid: int = masterid, cmd = 'uptime', node: str = proxmox_node, timeout: int = 600):

  # define kname
  kname = 'proxmox_qa-exec'

  # get vmname and the node the vm actually runs on
  vmname = vmnames[vmid]
  node = vms.get(vmid, node)

  # short command for the live line
  short_cmd = cmd if len(cmd) <= 60 else cmd[:57] + '...'

  with kstep(kname, f'{vmname} waiting for agent', quiet = True) as step:

    # wait for the agent - can be slow on first boot
    for _ in range(120):
      try:
        prox.nodes(node).qemu(vmid).agent.ping.post()
        break
      except:
        time.sleep(1)
    else:
      kabort(kname, f'agent not responding on {vmname} [{node}] cmd: {cmd}')

    # agent is up - show the command while it runs
    step.msg = f'{vmname} {short_cmd}'

    # send command
    try:
      exec_ret = prox.nodes(node).qemu(vmid).agent.exec.post(command = "bash -c '" + cmd + "'")
    except Exception as e:
      kabort(kname, f'problem running cmd: {cmd}\n{e}')

    # poll until the command exits
    pid = exec_ret['pid']
    waited = float(0)
    while True:
      try:
        pid_check = prox.nodes(node).qemu(vmid).agent('exec-status').get(pid = pid)
      except Exception as e:
        kabort(kname, f'problem with pid: {pid} {cmd}\n{e}')
      if pid_check['exited'] == 1:
        break
      time.sleep(0.5)
      waited += 0.5
      if waited >= timeout:
        kabort(kname, f'timed out after {timeout}s on {vmname}: {cmd}')

  # check for exitcode 127
  if int(pid_check['exitcode']) == 127:
    kabort(kname, f'exit code 127: {pid} {cmd}')

  out = (pid_check.get('out-data') or '').strip()
  err = (pid_check.get('err-data') or '').strip()

  # stderr - report and return stdout if there is any
  if err:
    kmsg('proxmox_qa-stderr', f'{cmd}\n{err}', 'err')
    if out:
      return out
    exit(1)

  # this is where data gets returned for an OK command
  if out:
    return out
  return 'no output-' + cmd
```

- [ ] **Step 2: `qa_write` error path (lines 136-138)**

```python
  except:
    kabort(kname, f'unable to write {remote_path} to {vmnames[vmid]}')
```

- [ ] **Step 3: Rewrite `node_reboot_wait` (lines 143-170)**

```python
# reboot a node via the agent and wait for it to return
def node_reboot_wait(vmid: int):

  # define kname
  kname = 'proxmox_reboot'
  vmname = vmnames[vmid]

  with kstep(kname, f'rebooting {vmname}'):

    # note the current boot id - microvms reboot in about a second so watching
    # for the agent to go down is a race we can lose
    boot_id = qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id')

    # transient timer so the exec returns before the agent goes away
    qa_exec(vmid, 'systemd-run --on-active=1 systemctl reboot 2>/dev/null')

    # wait for a new boot id
    for count in range(60):
      time.sleep(2)
      try:
        if qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id') != boot_id:
          break
      except:
        pass
    else:
      kabort(kname, f'{vmname} did not reboot')
```

- [ ] **Step 4: `node_prepare` — wrap in a step (edits within lines 172-281)**

Rename kname to `'proxmox_prepare'`, delete the `kmsg(kname, f'configuring {vmname}')` line, and wrap everything after the already-prepared check in `with kstep(kname, f'configuring {vmname}'):` (indent the whole body one level — from the `# neutralise pve-microvm first boot services` comment through the `extra_packages` block inclusive). Inside the block two error-path conversions:

```python
    # verify static ip applied
    ip_out = qa_exec(vmid, 'ip -4 addr show')
    if not re.search(vmip(vmid), ip_out):
      kabort(kname, f'{vmname} static ip {vmip(vmid)} not configured')
```

(The `internet_check(vmid)` calls stay as-is — Step 8 makes internet_check abort itself.)

- [ ] **Step 5: `prox_destroy` and `clone`**

`prox_destroy` (lines 283-304): kname `'proxmox_destroy'`, wrap the stop+delete in a step, honor the vm's node for the image branch too:

```python
# stop and destroy vm
def prox_destroy(vmid: int):

    kname = 'proxmox_destroy'

    # get node and vmname
    vmname = vmnames[vmid]
    node = vms[vmid]

    # if destroying image
    if vmid == cluster_id:
      prox_task(prox.nodes(node).qemu(cluster_id).delete(), node)
      return

    # power off and delete
    with kstep(kname, f'destroying {vmname}'):
      try:
        prox_task(prox.nodes(node).qemu(vmid).status.stop.post(), node)
        prox_task(prox.nodes(node).qemu(vmid).delete(), node)
      except Exception as e:
        kabort(kname, f'unable to destroy {node}/{vmid}\n{e}')
```

`clone` (lines 306-346): wrap the four `prox_task` calls in one step, keep `node_prepare` outside it so it gets its own ✓ line, tick the plan once at the end:

```python
  # clone
  with kstep('proxmox_clone', f'building {hostname}'):
    prox_task(prox.nodes(proxmox_node).qemu(cluster_id).clone.post(newid = vmid))

    # configure
    prox_task(prox.nodes(proxmox_node).qemu(vmid).config.post(
      name = hostname,
      onboot = 1,
      cores = vm_cpu,
      memory = memory,
      balloon = '0',
      net0 = (f'model=virtio,bridge={network_bridge}'),
      description = (f'{vmid}:{hostname}:{ip}')
    ))

    # resize disk
    prox_task(prox.nodes(proxmox_node).qemu(vmid).resize.put(
      disk = 'scsi0',
      size = f'{vm_disk}G',
    ))

    # power on
    prox_task(prox.nodes(proxmox_node).qemu(vmid).status.start.post())

  # configure the node via the guest agent
  node_prepare(vmid)

  # one plan unit - clone + prepare
  kplan_tick()
```

(Delete the old `kmsg('proxmox_clone', f'building {hostname}')` line.)

- [ ] **Step 6: Rewrite `prox_task` (lines 348-365)** — add the poll sleep, honor `node`, add a timeout:

```python
# proxmox task blocker
def prox_task(task_id, node = proxmox_node, timeout: int = 600):

  # define kname
  kname = 'proxmox_task'

  # task type out of the upid for the live line
  try:
    task_type = task_id.split(':')[5]
  except:
    task_type = str(task_id)

  with kstep(kname, f'{task_type} on {node}', quiet = True):
    waited = float(0)
    while True:
      try:
        status = prox.nodes(node).tasks(task_id).status.get()
      except Exception as e:
        kabort(kname, f'unable to get task {task_id} node: {node}\n{e}')
      if status['status'] == 'stopped':
        break
      time.sleep(0.5)
      waited += 0.5
      if waited >= timeout:
        kabort(kname, f'{task_type} timed out after {timeout}s on {node}')

  # if task not completed ok
  if not status['exitstatus'] == 'OK':
    kabort(kname, (f'task exited with non OK status ({status["exitstatus"]})\n' + task_log(task_id, node)))
```

- [ ] **Step 7: Rewrite `task_log` (lines 367-387)** — honor `node`, drop the unreachable return, and never abort (it runs inside prox_task's failure path — losing the original error for a log-fetch failure is worse):

```python
# returns the task log
def task_log(task_id, node = proxmox_node):

  # define empty log line
  logline = ''

  # append each log line - a log fetch failure must not mask the task error
  try:
    for log in prox.nodes(node).tasks(task_id).log.get():
      logline += log['t'] + '\n'
  except:
    kmsg('proxmox_task-log', f'failed to get log for task {task_id}', 'sys')

  # return string
  return(logline)
```

- [ ] **Step 8: `internet_check` (lines 389-398)**

```python
  # if curl command fails
  if internet_check == 'error':
    kabort('proxmox_netcheck', f'{vmname} internet access check failed')
```

- [ ] **Step 9: Verify**

Run: `python3 -m py_compile lib/kopsrox_proxmox.py && ./kopsrox.py cluster info && ./kopsrox.py cluster info | cat`
Expected: compile clean; tty run shows quiet qa_exec spinners while kubectl polls, then the info lines; piped run shows plain lines only (no ANSI, no spinner lines).

- [ ] **Step 10: Commit**

```bash
git add lib/kopsrox_proxmox.py
git commit -m "kopsrox_proxmox: kstep spinners on wait sites, fix prox_task hot loop and ignored node arg, qa_exec timeout + real error reporting"
```

---

### Task 4: `lib/kopsrox_k3s.py` — install/ready steps, plan helper, sweep

**Files:**
- Modify: `lib/kopsrox_k3s.py`

**Interfaces:**
- Consumes: `kstep`, `kabort`, `kplan_tick`, `conf_check_master_up`.
- Produces: `cluster_plan_total()` → int, used by Task 5's `verb_cluster.py`. `k3s_init_node`/`k3s_remove_node` tick the plan (init = 1 unit incl. the already-Ready skip path, remove = 1 unit, master export = 1 unit).

- [ ] **Step 1: Rewrite `k3s_init_node` (lines 22-86)**

```python
# create a master/slave/worker
def k3s_init_node(vmid: int = masterid, nodetype = 'master', snapshot = 'kopsrox'):

  # nodetype error check
  if nodetype not in ['master', 'slave', 'worker', 'restore']:
    kabort('k3s_init-node', f'{nodetype} invalid nodetype')

  # check node has internet - aborts itself on failure
  internet_check(vmid)

  # map vmname
  vmname = vmnames[vmid]

  # k3s already up on this node
  if k3s_check(vmid):
    kmsg(f'k3s_{nodetype}', f'{vmname} Ready')
    kplan_tick()
    return

  # master / slave / worker
  if nodetype in ['master', 'worker', 'slave']:
    step_msg = f'installing {k3s_version} on {vmname}'
    init_cmd = f'/root/scripts/kopsrox.sh {nodetype} {vmid} {get_k3s_token()}'

  # restore
  if nodetype == 'restore':
    if snapshot == 'kopsrox':
      bs_cmd = f'/root/scripts/kopsrox.sh latest {masterid} {get_k3s_token()}'
      bs_cmd_out = qa_exec(masterid, bs_cmd)

      # sort ls output so last is latest snapshot
      for snap in sorted(bs_cmd_out.split('\n')):
        if re.search(f'kopsrox-{cluster_name}', snap.split()[0]):
            latest = snap.split()[0]
      snapshot = latest

    step_msg = f'restoring {snapshot}'
    init_cmd = f'/root/scripts/kopsrox.sh restore {snapshot} {get_k3s_token()}'

  # write log of install on node
  init_cmd = init_cmd + f' > /k3s_{nodetype}_install.log 2>&1'

  with kstep(f'k3s_{nodetype}', step_msg) as step:

    # run command
    qa_exec(vmid, init_cmd)

    # wait until ready - each k3s_check is a kubectl run so takes a second or two
    step.msg = f'waiting for {vmname} Ready'
    wait: int = 20
    for count in range(wait):
      if k3s_check(vmid):
        break
      time.sleep(1)
    else:
      kabort('k3s_check', f'timed out after {wait}s for {vmname}')

  kplan_tick()

  # final steps for first master / restore export kubeconfig and token
  if nodetype in ['master', 'restore']:
    with kstep('k3s_export', 'kubeconfig + token'):
      kubeconfig()
      export_k3s_token()
    kplan_tick()
```

- [ ] **Step 2: `k3s_remove_node` (lines 88-103)** — wrap in a step + tick:

```python
# remove a node
def k3s_remove_node(vmid: int):

  # get vmname
  vmname = vmnames[vmid]

  with kstep('k3s_remove-node', vmname):
    if vmname != f'{cluster_name}-m1':
      kubectl('cordon ' + vmname)
      kubectl('drain --timeout=10s --delete-emptydir-data --ignore-daemonsets --force ' + vmname)
      kubectl('delete node ' + vmname)
      # remove the node password secret or a rebuilt node with this name gets rejected
      kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found')

    # destroy vm
    prox_destroy(vmid)

  kplan_tick()
```

- [ ] **Step 3: `k3s_rm_cluster` m1 branch (lines 119-120)** — the direct destroy is one plan unit too:

```python
    # remove node from cluster and proxmox
    if vmname == f'{cluster_name}-m1':
      prox_destroy(vmid)
      kplan_tick()
    else:
      k3s_remove_node(vmid)
```

- [ ] **Step 4: Add `cluster_plan_total()`** — insert directly above `k3s_update_cluster` (line 124):

```python
# best effort plan unit count for the progress bar - mirrors k3s_update_cluster
# units: 1 per missing node ( clone + prepare ), 1 per target k3s init/check,
# 1 for kubeconfig/token export when the master needs installing, 1 per removal
def cluster_plan_total():

  vmids = list_kopsrox_vm()

  # target nodes per the ini
  targets = [masterid]
  if masters == 3:
    targets += [masterid + 1, masterid + 2]
  workerid = masterid + 3
  targets += [workerid + count for count in range(1, workers + 1)]

  total = 0
  for target in targets:
    if target not in vmids:
      total += 1
    total += 1

  # kubeconfig / token export happens when the master actually installs
  if not conf_check_master_up:
    total += 1

  # removals - extra masters and anything past the last configured worker
  last_worker = workerid + workers
  for vmid in vmids:
    if masters == 1 and vmid in (masterid + 1, masterid + 2):
      total += 1
    if vmid > last_worker:
      total += 1

  return total
```

- [ ] **Step 5: Sweep the rest of the module**

- `export_k3s_token` (lines 236-238): `kabort('k3s_export-token', 'passwords different between live system and local token! exiting')`
- `cluster_info` (lines 262-264) — `kname` here silently resolves to a module global from the star-import chain; name it properly:

```python
  # check m1 id exists
  if not masterid in cluster_info_vms:
    kabort('cluster_info', f'cluster {cluster_name} does not exist')
```

- [ ] **Step 6: Verify**

Run: `python3 -m py_compile lib/kopsrox_k3s.py && ./kopsrox.py cluster info`
Expected: compile clean; info renders as before with new formatting.

- [ ] **Step 7: Commit**

```bash
git add lib/kopsrox_k3s.py
git commit -m "kopsrox_k3s: kstep install/ready/remove steps, cluster_plan_total for the progress bar, kabort sweep"
```

---

### Task 5: Verbs — step plans + exit-code sweep

**Files:**
- Modify: `lib/verb_cluster.py`, `lib/verb_image.py`, `lib/verb_etcd.py`, `lib/verb_node.py`

**Interfaces:**
- Consumes: `kplan(add, title)`, `kplan_tick()`, `kstep`, `kabort`, `cluster_plan_total()`.
- Produces: nothing consumed later — these are the CLI leaves.

- [ ] **Step 1: `lib/verb_cluster.py` — plans for update/restore/create/destroy**

update (lines 17-18):

```python
# update cluster
if cmd == 'update':
  kplan(cluster_plan_total(), f'{cluster_name} cluster update')
  k3s_update_cluster()
```

restore (lines 21-38) — only the plan line is new, body unchanged:

```python
# restore from latest etcd snapshot
if cmd == 'restore':

  # removals + m1 clone/restore-init/export + m1 recheck + rebuilt slaves and workers
  removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
  kplan(removals + 4 + 2 * (masters - 1) + 2 * workers, f'{cluster_name} cluster restore')

  k3s_rm_cluster()
  ...rest of the existing block unchanged...
```

create (lines 41-52) — `+ 1` because the master init runs in the verb and again inside `k3s_update_cluster` (instant Ready recheck, still a tick):

```python
# create new cluster / master server
if cmd == 'create':

  kplan(cluster_plan_total() + 1, f'{cluster_name} cluster create')

  # if masterid not found running
  if not masterid in list_kopsrox_vm():
    kmsg(kname,f'{cluster_name} id {cluster_id} network {network_ip} m {masters} w {workers}', 'sys')
    clone(masterid)

  # install k3s on master
  k3s_init_node()

  # perform rest of cluster creation
  k3s_update_cluster()
```

destroy (lines 55-57):

```python
# destroy the cluster
if cmd == 'destroy':
  removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
  kplan(removals, f'{cluster_name} cluster destroy')
  kmsg(kname, f'{cluster_name}', 'err')
  k3s_rm_cluster()
```

- [ ] **Step 2: `lib/verb_image.py` — plan + steps around the four build stages, kabort sweep**

After the `kmsg(f'{kname}create', ...)` header line (line 60) add:

```python
  # template build / rootfs verify / kernel args / tag
  kplan(4, f'{cluster_name} image create')
```

Error-path conversions in the create block:
- pve-microvm missing (l.63-65): `kabort(f'{kname}check', 'pve-microvm not installed - see https://github.com/rcarmo/pve-microvm')`
- kernel missing (l.68-70): `kabort(f'{kname}check', f'{microvm_kernel} not found - run dev/build-kopsrox-kernel.sh')`
- k3s download failure (l.79-81): `kabort(f'{kname}check', f'unable to download get k3s script')`
- patch failure in `patch_microvm_template` (l.47-49): `kabort(f'{kname}patch', f'pve-microvm-template patch failed - upstream changed?\n{old}')`
- rootfs missing (l.115-117): `kabort(f'{kname}check', 'template rootfs is missing systemd/qemu-ga - check kopsrox-image.log')`

Wrap the long-running template build (lines 96-100):

```python
  # build the microvm template with a patched copy of pve-microvm-template
  microvm_template = patch_microvm_template()
  with kstep(f'{kname}template', f'running {microvm_template} ( log: kopsrox-image.log )'):
    local_exec(f'sudo bash {microvm_template} --image {oci_image} --vmid {cluster_id} \
--name {cluster_name}-i0 --storage {proxmox_storage} --disk-size 2G --memory 1024 \
--cores 1 --profile standard --no-docker > kopsrox-image.log 2>&1')
  kplan_tick()
```

Wrap the rootfs verify (lines 102-119) in `with kstep(f'{kname}verify', 'checking template rootfs'):` (the `img_dev`/`img_check` local_execs move inside; the two `re.search` result checks stay after the with-block) followed by `kplan_tick()`.

After the `qm set --args` local_exec (line 123-124) add `kplan_tick()`; after the tag/describe `prox_task` (line 135-138) add `kplan_tick()`.

- [ ] **Step 3: `lib/verb_etcd.py` — kabort sweep**

- master missing (l.14-18): `kabort(f'{kname}-check', 'cluster does not exist')`
- token problem (l.21-25): `kabort(f'{kname}-check', 'problem with k3s token')`
- s3_run fatal (l.35-37): `kabort(f'{kname}-s3run', f'\n {s3_out}')`
- s3 connect failure (l.65-69): `kabort(f'{kname}-check', 'error getting data from s3 repo')`
- snapshot not found (l.107-111) — s3_list must still print, so not kabort:

```python
  # check passed snapshot name exists
  if not re.search(snapshot,snapshots):
    kmsg(kname, f'{snapshot} not found', 'err')
    s3_list()
    exit(1)
```

- [ ] **Step 4: `lib/verb_node.py` — two small fixes**

- cluster-exec (l.26): `exit(1)` → `exit(0)` (it is a success path).
- vm not found (l.72) currently prints and falls through into the utility block: `kabort(kname, f'{arg} vm not found')`

- [ ] **Step 5: Verify**

Run: `for f in kopsrox.py lib/*.py; do python3 -m py_compile $f; done && ./kopsrox.py cluster info && ./kopsrox.py k3s kubectl "get nodes" && ./kopsrox.py node ssh nope; echo "exit=$?"`
Expected: all compile; info + kubectl render; the bogus hostname prints `✗ node:ssh nope vm not found` and `exit=1`.

- [ ] **Step 6: Commit**

```bash
git add lib/verb_cluster.py lib/verb_image.py lib/verb_etcd.py lib/verb_node.py
git commit -m "verbs: kplan progress bars for cluster/image, kabort exit-1 sweep"
```

---

### Task 6: CLAUDE.md + final verification

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:** none — documentation + end-to-end check.

- [ ] **Step 1: Update CLAUDE.md**

Replace the deps line:

```
- Python deps: `proxmoxer`, `requests`, `termcolor`, `urllib3` (no requirements.txt — install manually).
```

with:

```
- Python deps: `proxmoxer`, `requests`, `urllib3` (no requirements.txt — install manually).
```

Replace the kmsg module bullet:

```
- `lib/kopsrox_kmsg.py` — all user output goes through `kmsg(kname, msg, sev)`. `kname` is `scope_action` (split on the first `_` for coloring); `sev` is `info` (default), `sys`, or `err`.
```

with:

```
- `lib/kopsrox_kmsg.py` — the only module that emits ANSI; all user output goes through it. `kmsg(kname, msg, sev)` prints a glyph + severity-colored line (`kname` is `scope_action`, split on the first `_`; `sev` is `info`/`sys`/`err`); `kabort(kname, msg)` is `err` + `exit(1)`; `kstep(kname, msg)` is a context manager showing a live spinner with elapsed time (`quiet=True` for polling internals like `qa_exec`/`prox_task` — nothing printed on success); `kplan(add, title)`/`kplan_tick()` drive the overall `4/9` progress bar in compound verbs (`cluster create/update/restore/destroy`, `image create`). Output degrades to plain sequential lines when stdout is not a tty; `NO_COLOR` disables color. `dev/test_kmsg.py` tests the module standalone (scripted asserts piped, visual demo on a tty).
```

Replace the error-handling paragraph:

```
Error-handling convention: broad `try/except` with `kmsg(..., 'err')` followed by `exit(0)` — errors deliberately exit with status 0, and control flow frequently relies on `except` around attribute/index access.
```

with:

```
Error-handling convention: errors go through `kabort(kname, msg)` — an `err` line then `exit(1)`; success paths exit 0. Broad `try/except` around attribute/index access is still common, but SystemExit-as-control-flow (an `exit()` inside `try` caught by a bare `except`) has been removed — don't reintroduce it.
```

- [ ] **Step 2: Full verification pass**

```bash
for f in kopsrox.py lib/*.py; do python3 -m py_compile $f; done
./dev/test_kmsg.py | cat            # kmsg tests OK
./kopsrox.py                        # usage, new format
./kopsrox.py cluster info           # tty rendering against the live cluster
./kopsrox.py cluster info | cat     # plain lines, zero ansi: verify with | grep -c $'\x1b' → 0
grep -rn termcolor lib/ kopsrox.py  # no hits
```

Expected: everything above as annotated; `echo $?` after a deliberate failure (e.g. `./kopsrox.py node ssh nope`) is 1.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "update CLAUDE.md - kmsg module surface, exit-1 error convention, drop termcolor"
```
