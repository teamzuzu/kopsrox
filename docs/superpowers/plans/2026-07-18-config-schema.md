# Config Schema + Staged Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One SCHEMA drives ini validation and default-ini generation, and import-time checks collapse to 2 baseline API calls with verb-scoped extras, per `docs/superpowers/specs/2026-07-18-config-management-design.md`.

**Architecture:** New pure module `lib/kopsrox_schema.py` (SCHEMA + validators + `validate()` + `render_ini()`, no proxmox/argv/side effects — it must NOT import `kopsrox_config`, because `kopsrox_ini.py` runs exactly when `kopsrox.ini` is missing). `lib/kopsrox_config.py` keeps its import-time execution and exact global names but becomes staged: validate → connect → one-discovery-call → verb-scoped checks. `lib/kopsrox_ini.py` becomes a renderer wrapper.

**Tech Stack:** Python 3 stdlib + existing deps only (`proxmoxer`, `requests`, `urllib3`).

## Global Constraints

- Flat globals stay: consumers keep `from kopsrox_config import *` and today's names — `cluster_name`, `cluster_id`, `masterid`, `prox`, `vms`, `vmnames`, `suffixes`, `network_*`, `region`, `region_string`, `s3_endpoint`, `access_key`, `access_secret`, `bucket`, `oci_image`, `microvm_kernel`, `microvm_initrd`, `conf_check_master_up`, `passed_cmd`, `disc_nodes`, plus functions `kopsrox_img`, `list_kopsrox_vm`, `get_k3s_token`, `vmip`, `local_exec`, `image_info`.
- All abort messages keep today's exact text; all aborts via `kabort` (exit 1). kname is `config_check` until cluster_name resolves, then `<cluster_name>_config-check`.
- 2-space indentation, lowercase informal comments.
- ini-name → global-name mapping (hyphens aren't identifiers): `s3_region`→`region`, `s3_access-key`→`access_key`, `s3_access-secret`→`access_secret`, `s3_bucket`→`bucket`; all others map to themselves.
- Approved behavior changes (only these): per-verb power-on scoping; agent-ping master probe (no in-guest exec at import; the `k3s down but master server available...?` warning disappears); all options validated for every verb (missing `oci_image` now aborts everywhere); vm_cpu floor actually enforced; commented-out ini options (`microvm_kernel`, `microvm_initrd`, `s3_region`) resolve to defaults instead of today's abort-if-uncommented-and-missing trap.
- Live verification runs against the real Proxmox host (kopsrox.ini here is live). No cluster currently exists — `cluster info` aborting with "cluster anchovy does not exist" is the expected healthy output.
- Never commit as Claude; repo-style short lowercase commit messages, no attribution footers.

---

### Task 1: `lib/kopsrox_schema.py` + `dev/test_config.py`

**Files:**
- Create: `lib/kopsrox_schema.py`
- Test: `dev/test_config.py`

**Interfaces:**
- Consumes: `kabort` from `kopsrox_kmsg` (Task 1 of the kmsg plan, already merged).
- Produces: `SCHEMA` (ordered list of dicts with keys `name, comment, default, kind, blank_ok, commented, check, var, ini_value`), `validate(parser) -> dict` (global-name → typed value, aborts exit-1 on bad config), `render_ini() -> ConfigParser` (default ini with comments, same `allow_no_value` technique as today). Task 2 uses `render_ini`; Task 3 uses `validate`.

- [ ] **Step 1: Write the failing test**

Create `dev/test_config.py`:

```python
#!/usr/bin/env python3

# checks for lib/kopsrox_schema.py - pure, no proxmox needed
# run: ./dev/test_config.py

import sys, io, contextlib
sys.path[0:0] = ['lib/']

from configparser import ConfigParser
from kopsrox_schema import SCHEMA, validate, render_ini

# render a fresh parser from the schema defaults
def fresh_parser():
  rendered = io.StringIO()
  render_ini().write(rendered)
  parser = ConfigParser()
  parser.read_string(rendered.getvalue())
  # default endpoint is deliberately localhost so users must edit it - patch for clean runs
  parser.set('kopsrox', 'proxmox_endpoint', '192.168.0.5')
  return parser

# expect validate() to abort with exit code 1
def expect_abort(mutate):
  parser = fresh_parser()
  mutate(parser)
  try:
    with contextlib.redirect_stdout(io.StringIO()):
      validate(parser)
  except SystemExit as e:
    assert e.code == 1, f'abort should exit 1 not {e.code}'
    return
  assert False, 'expected validate() to abort'

# rendered ini contains every non-commented option and no commented ones
rendered_config = render_ini()
for entry in SCHEMA:
  if entry['commented']:
    assert not rendered_config.has_option('kopsrox', entry['name']), entry['name']
  else:
    assert rendered_config.has_option('kopsrox', entry['name']), f"{entry['name']} missing from rendered ini"

# defaults round-trip through validate ( with the endpoint patched )
values = validate(fresh_parser())
assert values['cluster_name'] == 'mycluster', values['cluster_name']
assert values['cluster_id'] == 620 and type(values['cluster_id']) is int
assert values['masters'] == 1 and type(values['masters']) is int
assert values['region'] == '', 'commented s3_region should fall back to empty'
assert values['access_key'] == 'e3898d39d39id93', 'hyphenated ini name must map to access_key'
assert values['microvm_kernel'] == '/usr/share/pve-microvm/vmlinuz-kopsrox'
assert values['extra_packages'] == 'nfs-common'

# every SCHEMA var is a valid python identifier ( they become module globals )
for entry in SCHEMA:
  assert entry['var'].isidentifier(), entry['var']

# negative cases - each must abort with exit 1
expect_abort(lambda p: p.remove_option('kopsrox', 'cluster_name'))
expect_abort(lambda p: p.remove_option('kopsrox', 'k3s_version'))
expect_abort(lambda p: p.set('kopsrox', 'cloudinituser', ''))
expect_abort(lambda p: p.set('kopsrox', 'vm_ram', 'x'))
expect_abort(lambda p: p.set('kopsrox', 'masters', '2'))
expect_abort(lambda p: p.set('kopsrox', 'cluster_id', '99'))
expect_abort(lambda p: p.set('kopsrox', 'proxmox_endpoint', 'localhost'))
expect_abort(lambda p: p.set('kopsrox', 'cloudinitsshkey', 'notakey'))
expect_abort(lambda p: p.set('kopsrox', 'vm_cpu', '0'))
expect_abort(lambda p: p.set('kopsrox', 'vm_disk', '10'))
expect_abort(lambda p: p.set('kopsrox', 'vm_ram', '1'))

# blank allowed where blank is legal
parser = fresh_parser()
parser.set('kopsrox', 'extra_packages', '')
assert validate(parser)['extra_packages'] == ''

print('config schema tests OK')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `chmod +x dev/test_config.py && ./dev/test_config.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'kopsrox_schema'`

- [ ] **Step 3: Write the module**

Create `lib/kopsrox_schema.py`:

```python
#!/usr/bin/env python3

# kopsrox config schema - single source of truth for every kopsrox.ini option
# pure module: no proxmox, no argv, no side effects
# imported by kopsrox_config ( validation ), kopsrox_ini ( default ini ) and dev/test_config.py
# it must never import kopsrox_config - the default ini is generated exactly when kopsrox.ini is missing

from configparser import ConfigParser
from kopsrox_kmsg import kabort

# validators - called as check(kname, value) after type coercion
def check_endpoint(kname, value):
  if ( value == "localhost" or value == "127.0.0.1" ):
    kabort(kname, f'proxmox_endpoint cannot be localhost - please use a reachable IP')

def check_cluster_id(kname, value):
  if value < 100:
    kabort(kname, f'cluster_id is too low - should be over 100')

def check_vm_disk(kname, value):
  if value < 20:
    kabort(kname, f'vm_ - kopsrox vms need 20G disk')

def check_vm_cpu(kname, value):
  if value < 1:
    kabort(kname, f'vm_ - kopsrox vms at least 1 cpu')

def check_vm_ram(kname, value):
  if value < 2:
    kabort(kname, f'vm_ram - kopsrox vms need 2G RAM')

def check_sshkey(kname, value):
  if not value.startswith('ssh-'):
    kabort(kname, f'[kopsrox]/cloudinitsshkey - invalid ssh key')

def check_masters(kname, value):
  if not (value == 1 or value == 3):
    kabort(kname, f'[cluster] - masters: only 1 or 3 masters supported. You have: {value}')

# one option definition
# comment: None, a string or a list of strings - rendered as ; lines above the option
# commented: option ships commented out and resolves to default when absent
# var: the module global the value lands in ( ini names with hyphens need one )
# ini_value: literal text written to the default ini when it differs from default
def opt(name, comment, default, kind = str, blank_ok = False, commented = False, check = None, var = None, ini_value = None):
  return {'name': name, 'comment': comment, 'default': default, 'kind': kind, 'blank_ok': blank_ok,
          'commented': commented, 'check': check, 'var': var or name, 'ini_value': ini_value}

# every kopsrox.ini option in ini order - adding an option means adding one entry here
SCHEMA = [
  opt('proxmox_endpoint', 'domain or IP to access proxmox', '127.0.0.1', check = check_endpoint),
  opt('proxmox_api_port', 'api port ( usually 8006 ) ', '8006', kind = int),
  opt('proxmox_user', 'username to connect with / owner of the API token', 'root@pam'),
  opt('proxmox_token_name', 'name of api token', 'kopsrox'),
  opt('proxmox_token_value', 'text of api key', 'xxxxxxxxxxxxx'),
  opt('proxmox_node', 'the proxmox node that you will run kopsrox on - the image and all nodes are created on this host', 'proxmox'),
  opt('proxmox_storage', 'the proxmox storage to use for kopsrox - needs to be available on the proxmox node', 'local-lvm'),
  opt('oci_image', 'the OCI image used to build the microvm template ( via pve-microvm-template )', 'ubuntu:24.04'),
  opt('microvm_kernel', 'kernel/initrd used to boot kopsrox microvms - built with dev/build-kopsrox-kernel.sh',
      '/usr/share/pve-microvm/vmlinuz-kopsrox', commented = True),
  opt('microvm_initrd', None, '/usr/share/pve-microvm/initrd-kopsrox', commented = True),
  opt('extra_packages', 'comma seperated list of extra packages installed into each node when created ', 'nfs-common', blank_ok = True),
  opt('vm_disk', 'size of vm disk in Gib ', '20', kind = int, check = check_vm_disk),
  opt('vm_cpu', 'number of cpu cores ', '1', kind = int, check = check_vm_cpu),
  opt('vm_ram', 'amount of ram in Gib ', '2', kind = int, check = check_vm_ram),
  opt('cloudinituser', 'username for the user created in each node ( via the guest agent )', 'user'),
  opt('cloudinitpass', 'password for the created user', 'admin'),
  opt('cloudinitsshkey', 'ssh public key for the created user ( required )', 'ssh-rsa cioieocieo', check = check_sshkey),
  opt('network_bridge', ['network bridge to use with kopsrox',
      'a proxmox sdn can be used by specifying the zone and vnet like this: sdn/zone/vnet'], 'vmbr0'),
  opt('network_ip', 'first ip of the ip range used for this kopsrox cluster', '192.168.0.160'),
  opt('network_mask', '/24 is 255.255.255.0', '24'),
  opt('network_gw', 'default gateway for the network ( needs to provide internet access ) ', '192.168.0.1'),
  opt('network_dns', 'dns server for network', '192.168.0.1'),
  opt('network_mtu', ['interface mtu applied inside each node ', 'set to 1450 if using sdn '], '1500', kind = int),
  opt('cluster_id', 'id for the cluster vm\'s eg from 620 - 630', '620', kind = int, check = check_cluster_id),
  opt('cluster_name', 'name of the cluster', 'mycluster'),
  opt('masters', 'number of masters nodes 1 or 3', '1', kind = int, check = check_masters),
  opt('workers', 'number of workers nodes 1 to 5', '1', kind = int),
  opt('k3s_version', 'k3s version', 'v1.34.5+k3s1'),
  opt('s3_endpoint', 's3 endpoint', 'kopsrox'),
  opt('s3_region', 's3 region - leave as \'\' for no region', '', commented = True, var = 'region', ini_value = '\'\''),
  opt('s3_access-key', 's3 access key', 'e3898d39d39id93', var = 'access_key'),
  opt('s3_access-secret', 's3 access secret', 'ioewioeiowe', var = 'access_secret'),
  opt('s3_bucket', 's3 bucket', 'kopsrox-backup', var = 'bucket'),
]

# validate a parsed kopsrox.ini against the schema
# returns { var: typed value } - aborts with todays messages on any problem
def validate(parser):

  # resolve cluster_name first so later messages carry it
  kname = 'config_check'
  values = {}

  def resolve(entry):
    name = entry['name']

    # commented options fall back to their default when absent
    if entry['commented']:
      raw = parser.get('kopsrox', name, fallback = entry['default'])
    else:
      if not parser.has_option('kopsrox', name):
        kabort(kname, f'{name} is missing in kopsrox.ini')
      raw = parser.get('kopsrox', name)
      if raw == '' and not entry['blank_ok']:
        kabort(kname, f'{name} - a value is required')

    # int options
    if entry['kind'] is int:
      try:
        raw = int(raw)
      except:
        kabort(kname, f'{name} should be numeric: {raw}')

    # option specific check
    if entry['check']:
      entry['check'](kname, raw)
    return raw

  by_name = {entry['name']: entry for entry in SCHEMA}
  values['cluster_name'] = resolve(by_name['cluster_name'])
  kname = f"{values['cluster_name']}_config-check"

  for entry in SCHEMA:
    if entry['name'] == 'cluster_name':
      continue
    values[entry['var']] = resolve(entry)

  return values

# build the default ini from the schema - same allow_no_value comment
# technique the old hand written generator used, so the format is unchanged
def render_ini():
  config = ConfigParser(allow_no_value = True)
  ks = 'kopsrox'
  config.add_section(ks)

  for entry in SCHEMA:
    comments = entry['comment']
    if isinstance(comments, str):
      comments = [comments]
    for comment in comments or []:
      config.set(ks, f'; {comment}')
    name = f"# {entry['name']}" if entry['commented'] else entry['name']
    value = entry['ini_value'] if entry['ini_value'] is not None else str(entry['default'])
    config.set(ks, name, value)

  return config
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./dev/test_config.py`
Expected: `config schema tests OK`

- [ ] **Step 5: Commit**

```bash
git add lib/kopsrox_schema.py dev/test_config.py
git commit -m "kopsrox_schema: single schema for ini options - validation, defaults, comments"
```

---

### Task 2: `lib/kopsrox_ini.py` becomes a renderer wrapper

**Files:**
- Modify: `lib/kopsrox_ini.py` (entire file)

**Interfaces:**
- Consumes: `render_ini()` from `kopsrox_schema` (Task 1).
- Produces: `init_kopsrox_ini()` — same name/behavior as today, called by `kopsrox.py:13` when `kopsrox.ini` is absent, and by `dev/gen_config.sh`.

- [ ] **Step 1: Capture the baseline ini from the old generator**

```bash
S=/tmp/claude-1000/-home-simonc-GIT-anchovy/3eafcdba-eacd-4e5a-99c0-cef8c29419c9/scratchpad
mkdir -p $S/inibase && cd $S/inibase
git -C /home/simonc/GIT/anchovy show HEAD:lib/kopsrox_ini.py > kopsrox_ini_old.py
python3 -c "import sys; sys.path[0:0]=['.']; from kopsrox_ini_old import init_kopsrox_ini; init_kopsrox_ini()"
mv kopsrox.ini kopsrox.ini.baseline
cd /home/simonc/GIT/anchovy
```

Expected: `$S/inibase/kopsrox.ini.baseline` exists.

- [ ] **Step 2: Replace the file**

New complete content of `lib/kopsrox_ini.py`:

```python
#!/usr/bin/env python3

# generate the default kopsrox.ini from the schema
def init_kopsrox_ini():

  from kopsrox_schema import render_ini

  # write config
  # file should not already exist...
  with open('kopsrox.ini', 'w') as cfile:
    render_ini().write(cfile)
  print('created kopsrox.ini please edit for your setup')
  return
```

- [ ] **Step 3: Verify generated ini matches the baseline byte-for-byte**

```bash
S=/tmp/claude-1000/-home-simonc-GIT-anchovy/3eafcdba-eacd-4e5a-99c0-cef8c29419c9/scratchpad
cd $S/inibase && rm -f kopsrox.ini
python3 -c "import sys; sys.path[0:0]=['/home/simonc/GIT/anchovy/lib']; from kopsrox_ini import init_kopsrox_ini; init_kopsrox_ini()"
diff kopsrox.ini kopsrox.ini.baseline && echo IDENTICAL
cd /home/simonc/GIT/anchovy
```

Expected: `IDENTICAL`. If the diff shows drift, fix the SCHEMA comments/values (they were copied verbatim from the old generator) — do not adjust the baseline.

- [ ] **Step 4: Commit**

```bash
git add lib/kopsrox_ini.py
git commit -m "kopsrox_ini: render default ini from the schema"
```

---

### Task 3: `lib/kopsrox_config.py` staged rewrite

**Files:**
- Modify: `lib/kopsrox_config.py` (lines 14-295 — everything between the kmsg import and `get_k3s_token`)

**Interfaces:**
- Consumes: `validate` from `kopsrox_schema`.
- Produces: identical global surface as today (see Global Constraints list). `conf_check()` is deleted — nothing outside this file calls it. The unused `config` dict comprehension (old line 18) is deleted — verified no consumers.

- [ ] **Step 1: Replace the parse/validate/derive section**

Replace old lines 14-58 (`# read ini file into config` through `masterid = int(cluster_id) + 1`) and fold in the old scattered derived blocks (network derivation old lines 179-182, region_string old lines 202-205, vmnames old lines 207-212 — delete them at their old positions) with:

```python
# read and validate kopsrox.ini against the schema - injects every option
# as a module global ( see SCHEMA in kopsrox_schema.py for the names )
from configparser import ConfigParser
from kopsrox_schema import validate
kopsrox_config = ConfigParser()
kopsrox_config.read('kopsrox.ini')
globals().update(validate(kopsrox_config))

# kname and the verb being run
kname = f'{cluster_name}_config-check'
passed_cmd = sys.argv[1]

# assign master id
masterid = int(cluster_id) + 1

# variables for network and its IP for vmip function
network_octs = network_ip.split('.')
network_base = f'{network_octs[0]}.{network_octs[1]}.{network_octs[2]}.'
network_ip_prefix = int(network_octs[-1])

# region optional
region_string = ''
if region:
  region_string = region

# define vmnames
suffixes = ['-i0', '-m1', '-m2', '-m3', '-u1', '-w1', '-w2', '-w3', '-w4', '-w5']
vmnames = {
  cluster_id + i: f"{cluster_name}{suffix}"
  for i, suffix in enumerate(suffixes)
}
```

Delete: the whole `conf_check()` function, every `x = conf_check('x')` line and its inline check (`cluster_id`, `proxmox_endpoint`, `vm_disk`, `vm_cpu`, `vm_ram`, `cloudinitsshkey`, `masters` blocks), the `config = ({s:dict...})` comprehension, and inside the image branch the `oci_image = conf_check('oci_image')` and the two `microvm_kernel`/`microvm_initrd` fallback lines (all now injected by `validate`).

- [ ] **Step 2: Keep connect, replace discovery**

The `ProxmoxAPI` connect block (old lines 71-86) stays word-for-word. Then replace the node check (old 88-92), k3s probe (94-105), storage check (107-110), bridge block (251-270), image-exists block (272-279), and power-on loop (281-295) with the staged version — final layout of the file after the connect block:

```python
# discover cluster state - one call covers nodes, storage, vms and the image
try:
  resources = prox.cluster.resources.get()
except Exception as e:
  kabort(kname, f'unable to list cluster resources\n{e}')

# map node name
disc_nodes = [r.get('node') for r in resources if r.get('type') == 'node']
if proxmox_node not in disc_nodes:
  kabort(kname, f'"{proxmox_node}" not found - discovered nodes: {disc_nodes}')

# storage
if not [r for r in resources if r.get('type') == 'storage' and r.get('node') == proxmox_node and r.get('storage') == proxmox_storage]:
  kabort(kname, f'{proxmox_storage} storage not found')

# kopsrox vms in the cluster id range - full resource entries kept for status/name
disc_vms = {int(r['vmid']): r for r in resources
            if r.get('type') == 'qemu' and cluster_id <= int(r['vmid']) < cluster_id + 10}

# vms var used in other code now and needs renaming
vms = {vmid: disc_vms[vmid].get('node') for vmid in sorted(disc_vms)}

# check the image exists - image create builds it so skips the check
if not (passed_cmd == 'image' and sys.argv[2:3] == ['create']):
  if cluster_id not in vms:
    kabort(kname, f'{cluster_name} image not found - please run "kopsrox image create"')

# guest verbs power on any stopped node
if passed_cmd in ['cluster', 'k3s', 'etcd', 'node']:
  for vmid in vms:
    if vmid != cluster_id and disc_vms[vmid].get('status') == 'stopped':
      kmsg(kname, f'powering on {disc_vms[vmid].get("name")}', 'sys')
      prox.nodes(vms[vmid]).qemu(vmid).status.start.post()

# master reachable? agent ping only - consumed by the image bridge gate and cluster plan totals
# an agent-alive proxy for k3s-alive: cluster_plan_total can over-count one export unit, the bar clamps
conf_check_master_up = False
if passed_cmd in ['image', 'cluster'] and disc_vms.get(masterid, {}).get('status') == 'running':
  try:
    prox.nodes(vms[masterid]).qemu(masterid).agent.ping.post()
    conf_check_master_up = True
  except:
    pass
```

- [ ] **Step 3: Move the image-only checks under one branch**

The existing image branch (old lines 112-144, minus the conf_check/fallback lines deleted in Step 1) moves to after the block above, followed by the bridge check (old lines 251-270) indented into it — final shape:

```python
# image related config checks
if passed_cmd == 'image':

  # pve-microvm version checks
  # kopsrox needs 0.3.19+ ( qm shutdown fix and the template layout we patch )
  microvm_ver = subprocess.run(['bash', '-c', "dpkg-query -W -f '${Version}' pve-microvm 2>/dev/null || echo none"], text=True, capture_output=True).stdout.strip()
  if microvm_ver == 'none':
    kabort(kname, 'pve-microvm is not installed - see docs/SETUP.md')
  microvm_installed = tuple(map(int, microvm_ver.split('-')[0].split('.')))
  if microvm_installed < (0, 3, 19):
    kabort(kname, f'pve-microvm {microvm_ver} is too old - kopsrox needs 0.3.19 or later')

  # notify if upstream has a newer release - skip quietly if offline
  try:
    microvm_latest_tag = requests.get('https://api.github.com/repos/rcarmo/pve-microvm/releases/latest', timeout=3).json()['tag_name']
    if tuple(map(int, microvm_latest_tag.lstrip('v').split('.'))) > microvm_installed:
      kmsg(kname, f'pve-microvm {microvm_latest_tag} is available ( installed: {microvm_ver} ) - restart pvedaemon after upgrading!', 'sys')
  except:
    pass

  # template may not exist yet on image create
  try:
    template_data = prox.nodes(proxmox_node).qemu(cluster_id).config.get()
    cloud_image_desc = template_data['description']
  except:
    cloud_image_desc = ''

  # check configured bridge exists or is a sdn vnet - skipped when the cluster is already live
  if not conf_check_master_up:
    if not re.search('sdn/', network_bridge):
      discovered_bridges = [bridge.get('iface', None) for bridge in prox.nodes(proxmox_node).network.get(type = 'bridge')]
    else:
      # check we can map zone and get vnets
      try:
        sdn_params = network_bridge.split('/')
        zone = sdn_params[1]
        network_bridge = sdn_params[2]
      except:
        kabort(kname, f'unable to parse sdn config: "{network_bridge}"')

      # discover available sdn bridges
      discovered_bridges = [bridge.get('vnet', None) for bridge in prox.nodes(proxmox_node).sdn.zones(zone).content.get()]

    # check configured bridge is in list
    if network_bridge not in discovered_bridges:
      kabort(kname, f'"{network_bridge}" not found. valid bridges: {discovered_bridges}')
```

The functions `kopsrox_img()`, `list_kopsrox_vm()`, `get_k3s_token()`, `vmip()`, `local_exec()`, `image_info()` remain unchanged at the bottom of the file (`kopsrox_img` no longer called at import — it stays for `image destroy`/`image_info` display).

- [ ] **Step 4: Verify live**

```bash
for f in kopsrox.py lib/*.py; do python3 -m py_compile $f; done && echo compile-ok
./dev/test_config.py && ./dev/test_kmsg.py | cat
./kopsrox.py cluster info; echo "exit=$?"          # expect: ✗ cluster:info cluster anchovy does not exist, exit=1
./kopsrox.py image info                            # expect: image desc + storage volid, exit 0
./kopsrox.py cluster info 2>&1 | grep -c $'\x1b'   # expect: 0
time ./kopsrox.py image info                       # note wall time vs pre-change (was 6+n API calls)
```

- [ ] **Step 5: Commit**

```bash
git add lib/kopsrox_config.py
git commit -m "kopsrox_config: schema validation + staged checks - one discovery call, verb scoped side effects"
```

---

### Task 4: CLAUDE.md + final verification

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:** none — docs + end-to-end check.

- [ ] **Step 1: Update CLAUDE.md**

In the architecture section, replace:

```
3. `lib/kopsrox_config.py` runs at import time: parses `kopsrox.ini`, validates every setting (`conf_check`), opens the Proxmox API connection (`prox`), verifies node/storage/bridge, and even powers on any stopped cluster VMs. Adding a config option means adding a `conf_check()` call here plus a default in `kopsrox_ini.py`. It also reads `sys.argv[1]` directly, so these modules cannot be imported outside the CLI entrypoint.
```

with:

```
3. `lib/kopsrox_config.py` runs at import time in stages: validates `kopsrox.ini` against the SCHEMA in `lib/kopsrox_schema.py` (injecting every option as a module global), opens the Proxmox API connection (`prox`), then makes ONE `cluster.resources` call that covers the node/storage/VM/image checks. Side effects are verb-scoped: guest verbs (`cluster`/`k3s`/`etcd`/`node`) power on stopped VMs; `image`+`cluster` ping the master agent (`conf_check_master_up`); image-only checks (pve-microvm version, bridge/SDN) run under `image`. Adding a config option means adding ONE `opt(...)` entry to `SCHEMA` in `lib/kopsrox_schema.py` — it drives validation, the global name (`var=` for ini names with hyphens), and the generated default ini (comments included; `lib/kopsrox_ini.py` just renders it). `kopsrox_schema.py` is pure and must never import `kopsrox_config` (the default ini is generated exactly when `kopsrox.ini` is missing); `kopsrox_config.py` reads `sys.argv[1]` directly, so it cannot be imported outside the CLI entrypoint. `dev/test_config.py` tests schema/renderer without touching Proxmox.
```

- [ ] **Step 2: Full verification pass**

```bash
for f in kopsrox.py lib/*.py; do python3 -m py_compile $f; done && echo compile-ok
./dev/test_config.py && ./dev/test_kmsg.py | cat
grep -rn "conf_check(" lib/ | grep -v conf_check_master_up   # expect: no hits
cd /tmp/claude-1000/-home-simonc-GIT-anchovy/3eafcdba-eacd-4e5a-99c0-cef8c29419c9/scratchpad/inibase && rm -f kopsrox.ini && python3 -c "import sys; sys.path[0:0]=['/home/simonc/GIT/anchovy/lib']; from kopsrox_ini import init_kopsrox_ini; init_kopsrox_ini()" && diff kopsrox.ini kopsrox.ini.baseline && echo IDENTICAL; cd /home/simonc/GIT/anchovy
./kopsrox.py image info && ./kopsrox.py cluster info; echo "exit=$?"
```

Expected: compile-ok, both test scripts pass, zero `conf_check(` call sites, IDENTICAL ini, image info exits 0, cluster info aborts red with exit 1 (no live cluster).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "update CLAUDE.md - schema driven config, staged verb scoped checks"
```
