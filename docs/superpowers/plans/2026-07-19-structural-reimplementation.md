# Structural Re-implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert kopsrox from import-side-effect execution to explicit `init()` + `run()` calls, with explicit imports, PEP 8 formatting, and type hints — behavior byte-identical.

**Architecture:** `kopsrox.py` validates argv, calls `kopsrox_config.init(verb, cmd)`, then `verb_<verb>.run(cmd, arg)`. Modules import exactly the names they use; call sites keep bare names. Import graph unchanged: config ← proxmox ← k3s ← verbs; artifacts ← config.

**Tech Stack:** Python 3.13, stdlib + proxmoxer/requests/urllib3 only. No new dependencies.

Spec: `docs/superpowers/specs/2026-07-19-structural-reimplementation-design.md`

## Global Constraints

- Behavior is FROZEN: CLI surface, kmsg output text, exit codes, ini format, generated artifacts, module/file names. Acceptance = byte-diff against Task 1 baselines.
- No `from x import *` anywhere when done. No `sys.argv` reads inside `lib/`.
- `kopsrox_schema.py` must never import `kopsrox_config`.
- Errors via `kabort(kname, msg)` (err line + exit 1); no bare `except:` that can catch SystemExit; use `except Exception` where a broad guard is genuinely needed.
- PEP 8: 4-space indent, `return x` not `return(x)`, `x not in y`, one stdlib module per import line. Single quotes. KEEP existing comments verbatim (they document microvm traps) unless the code they describe is deleted.
- Type hints on every function signature in `lib/` (params + return). No variable annotations.
- `kopsrox.ini` in this checkout points at a LIVE Proxmox host. Never run `image create`, `cluster create/destroy/update/restore`, `etcd` write commands, or `dev/rls_test.sh` — the only permitted live commands are `./kopsrox.py`, `./kopsrox.py <verb>` (help), `./kopsrox.py image info`, `./kopsrox.py cluster info`, and invalid-arg probes.
- Commit after each task with a plain message (user's gitconfig; no attribution footers).
- The working tree may have a temporarily broken CLI between Tasks 4 and 8 (old verb modules expect star-import re-exports). Only the checks listed in each task are expected to pass at that point.

---

### Task 1: Baseline capture

**Files:**
- Create: `dev/capture_baseline.sh`
- Create: `.baseline/` output directory (gitignored)
- Modify: `.gitignore` (add `.baseline/`)

**Interfaces:**
- Produces: `.baseline/<name>.txt` files + `.baseline/<name>.exit` files that Task 8 diffs against.

- [ ] **Step 1: Write the capture script**

```bash
#!/usr/bin/env bash
# capture pre-change behavior baselines - re-run on the OLD code only
set -e
B=.baseline
mkdir -p $B

cap() {  # cap <name> <cmd...>
    local name=$1; shift
    "$@" > $B/$name.txt 2>&1; echo $? > $B/$name.exit
}

cap help            ./kopsrox.py
cap help-cluster    ./kopsrox.py cluster
cap help-image      ./kopsrox.py image
cap help-etcd       ./kopsrox.py etcd
cap help-k3s        ./kopsrox.py k3s
cap help-node       ./kopsrox.py node
cap bad-verb        ./kopsrox.py bogus
cap bad-cmd         ./kopsrox.py etcd restore-latest
cap missing-arg     ./kopsrox.py etcd restore
cap image-info      ./kopsrox.py image info
cap cluster-info    ./kopsrox.py cluster info

# default ini from the schema renderer
./dev/gen_config.sh && mv kopsrox.ini.default $B/default.ini

# rendered artifacts for the live config ( argv hack needed on old code only )
python3 - <<'EOF'
import sys
sys.argv = ['kopsrox.py', 'k3s', 'kubeconfig']
sys.path.insert(0, 'lib')
from kopsrox_artifacts import kopsrox_manifest, k3s_server_config, kopsrox_sh
open('.baseline/artifact-manifest.yaml', 'w').write(kopsrox_manifest())
open('.baseline/artifact-config.yaml', 'w').write(k3s_server_config())
open('.baseline/artifact-kopsrox.sh', 'w').write(kopsrox_sh())
EOF
echo baseline captured
```

- [ ] **Step 2: Add `.baseline/` to .gitignore, run the script**

Run: `bash dev/capture_baseline.sh && ls .baseline/`
Expected: all files listed, no errors. `cluster-info.exit` may be 0 or 1 depending on whether a live cluster exists — either is fine, it just must match in Task 8.

- [ ] **Step 3: Commit**

```bash
git add dev/capture_baseline.sh .gitignore
git commit -m 'reimpl: baseline capture script'
```

---

### Task 2: kopsrox_kmsg.py — PEP 8 + type hints (no behavior change)

**Files:**
- Modify: `lib/kopsrox_kmsg.py` (whole-file reformat)

**Interfaces:**
- Produces: same public surface, now typed: `kmsg(kname: str = 'kopsrox', msg: str = 'no msg', sev: str = 'info') -> None`, `kabort(kname: str, msg: str) -> None`, `kstep(kname: str, msg: str, quiet: bool = False)` (context manager class), `kplan(add: int, title: str | None = None) -> None`, `kplan_tick() -> None`.

- [ ] **Step 1: Reformat**

Mechanical transformation of the existing file — logic, strings, and comments unchanged:
- Re-indent 2 → 4 spaces throughout.
- Add the type hints listed above plus internal helpers: `paint(text: str, color: str, bold: bool = False) -> str`, `fmt_kname(kname: str) -> str`, `fmt_secs(secs: float) -> str`, `fmt_line(sev: str, kname: str, msg: str) -> str`, `clip(line: str, width: int) -> str`, `clear_live() -> None`, `draw_live() -> None`, `emit(text: str) -> None`.
- Split `import sys, os, time, threading, atexit, shutil` into one module per line.
- No other edits.

- [ ] **Step 2: Verify**

Run: `python3 -m py_compile lib/kopsrox_kmsg.py && ./dev/test_kmsg.py | cat`
Expected: `kmsg tests OK`

- [ ] **Step 3: Commit**

```bash
git add lib/kopsrox_kmsg.py
git commit -m 'reimpl: kmsg pep8 + type hints'
```

---

### Task 3: kopsrox_schema.py + kopsrox_ini.py — PEP 8 + type hints

**Files:**
- Modify: `lib/kopsrox_schema.py`, `lib/kopsrox_ini.py`

**Interfaces:**
- Produces: `validate(parser: ConfigParser) -> dict`, `render_ini() -> ConfigParser`, `init_kopsrox_ini() -> None`. Same names, same behavior.

- [ ] **Step 1: Reformat both files**

Same mechanical rules as Task 2. Hint the validators (`check_endpoint(kname: str, value: str) -> None` etc — int checks take `value: int`) and `opt(...)` (`-> dict`). SCHEMA list content must not change by a single character inside the string literals.

- [ ] **Step 2: Verify — byte-identical default ini**

Run: `./dev/test_config.py && ./dev/gen_config.sh && diff kopsrox.ini.default .baseline/default.ini && echo INI-IDENTICAL`
Expected: `config schema tests OK` then `INI-IDENTICAL`

- [ ] **Step 3: Commit**

```bash
git add lib/kopsrox_schema.py lib/kopsrox_ini.py
git commit -m 'reimpl: schema + ini renderer pep8 + type hints'
```

---

### Task 4: kopsrox_config.py — explicit init(verb, cmd)

**Files:**
- Modify: `lib/kopsrox_config.py` (restructure + reformat)

**Interfaces:**
- Produces: `init(verb: str, cmd: str) -> None` — after it runs, these module attributes exist for import: every SCHEMA option name (`cluster_name`, `masters`, ... incl. `localuser/localpass/localsshkey`, s3 `var` names), `kname`, `masterid`, `network_base`, `network_ip_prefix`, `region_string`, `suffixes`, `vmnames`, `prox`, `resources`, `disc_nodes`, `disc_vms`, `vms`, `conf_check_master_up`; image-verb-only: `microvm_ver`, `cloud_image_desc`. Functions (importable any time): `kopsrox_img() -> str | bool`, `list_kopsrox_vm() -> dict[int, str]`, `get_k3s_token() -> str | None`, `vmip(vmid: int) -> str`, `local_exec(cmd: str) -> subprocess.CompletedProcess`, `image_info() -> None`.
- Consumes: `validate` from Task 3 (unchanged signature).

- [ ] **Step 1: Restructure**

The module body becomes: imports, then function definitions, then `init()`. NOTHING executes at import besides definitions. Every existing code block moves inside `init()` with its comments, in today's exact order; stage bodies are today's code re-indented. Skeleton (stage bodies elided here refer to the current file's lines, moved verbatim):

```python
#!/usr/bin/env python3

# external imports
import base64
import os
import re
import subprocess
import sys
import time
from configparser import ConfigParser
from datetime import datetime

import requests
import urllib3
from proxmoxer import ProxmoxAPI

from kopsrox_kmsg import kmsg, kabort, kstep, kplan, kplan_tick
from kopsrox_schema import validate

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def init(verb: str, cmd: str) -> None:
    # everything the old module did at import time, in the same order -
    # values land as module attributes via globals() so consumers can
    # 'from kopsrox_config import cluster_name' after init has run
    g = globals()

    # read and validate kopsrox.ini against the schema
    kopsrox_config = ConfigParser()
    kopsrox_config.read('kopsrox.ini')
    g.update(validate(kopsrox_config))

    g['kname'] = f'{g["cluster_name"]}_config-check'
    # ... [today's derived-value block: masterid, network_octs/base/prefix,
    #      region_string, suffixes, vmnames — assigned via g[...] ] ...
    # ... [today's connect block verbatim] ...
    # ... [today's discovery block: resources/disc_nodes/storage check/
    #      disc_vms/vms/image-exists check — 'passed_cmd' becomes the
    #      verb/cmd parameters: image-exists skip is
    #      `not (verb == 'image' and cmd == 'create')`] ...
    # ... [guest-verb power-on: `if verb in ['cluster', 'k3s', 'etcd', 'node']`] ...
    # ... [master ping: `if verb in ['image', 'cluster'] and ...`] ...
    # ... [image stage: `if verb == 'image'`] ...
```

Notes for the implementer:
- Local names inside init that today are plain assignments (`masterid = ...`) must become `g['masterid'] = ...` (or a `global` declaration listing them — pick ONE style, `g[...]` preferred for greppability). References within init can then use the local `g[...]` value or an intermediate local; keep it readable.
- `passed_cmd`/`sys.argv` must not appear anywhere in the file.
- Module-level functions (`kopsrox_img` etc) reference config attributes at call time (`prox`, `cluster_id`); since they only run after init, plain module-global references still resolve — keep their bodies as today, reformatted + typed.
- All kabort/kmsg message strings byte-identical to today.

- [ ] **Step 2: Verify with a live import test**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, 'lib')
import kopsrox_config as c
c.init('k3s', 'kubeconfig')
print(c.cluster_name, c.masterid, sorted(c.vms) if c.vms else 'no-vms')
print('ok')
"
```
Expected: cluster name + masterid printed, `ok`, exit 0. (Live read-only: connect + discovery + possible power-on of existing guest VMs — acceptable for verb `k3s` per today's behavior.)

- [ ] **Step 3: Commit**

```bash
git add lib/kopsrox_config.py
git commit -m 'reimpl: config init(verb, cmd) - no import side effects'
```

---

### Task 5: kopsrox_artifacts.py + kopsrox_proxmox.py — explicit imports

**Files:**
- Modify: `lib/kopsrox_artifacts.py`, `lib/kopsrox_proxmox.py`

**Interfaces:**
- Produces (unchanged names, now typed): artifacts — `kopsrox_manifest() -> str`, `k3s_server_config() -> str`, `kopsrox_sh() -> str`; proxmox — `qa_exec(vmid: int = ..., cmd: str = 'uptime', node: str = ..., timeout: int = 600) -> str`, `qa_write(vmid: int, remote_path: str, content: str, mode: str = '644') -> None`, `node_reboot_wait(vmid: int) -> None`, `node_prepare(vmid: int) -> None`, `internet_check(vmid: int) -> None`, `clone(vmid: int) -> None`, `prox_destroy(vmid: int) -> None`, `prox_task(task_id: str, node: str = ...) -> None` (match today's actual signature — read it first).
- Consumes: config attributes from Task 4 — imported explicitly.

IMPORTANT — default-argument trap: today `def qa_exec(vmid: int = masterid, ...)` captures `masterid` at def time; after Task 4 the module is imported AFTER init so the value exists, but a default bound at import stays correct only because init has already run. This works with the kopsrox.py ordering (Task 8) and the import-test ordering below; do NOT reorder imports so these modules load before init.

- [ ] **Step 1: Replace star imports with explicit lists**

For each file: delete `from kopsrox_config import *` (and artifacts' equivalents), add explicit imports of every config/kmsg name the file actually uses. Derivation method: `python3 -m pyflakes <file>` after deleting the star import lists every undefined name (pyflakes ships with the system python via pip? if unavailable: `python3 -c "import ast, sys; ..."` — simplest reliable method is: delete the star import, run `python3 -m py_compile`, then run the Task-level import test below and fix NameErrors until clean, then eyeball-grep for names only used inside f-strings). Reformat to PEP 8 + hints per Global Constraints.

- [ ] **Step 2: Verify**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, 'lib')
import kopsrox_config as c
c.init('k3s', 'kubeconfig')
import kopsrox_proxmox
import kopsrox_artifacts
open('/tmp/reimpl-artifact.sh', 'w').write(kopsrox_artifacts.kopsrox_sh())
print('ok')
" && diff /tmp/reimpl-artifact.sh .baseline/artifact-kopsrox.sh && echo ARTIFACT-IDENTICAL
```
Expected: `ok`, `ARTIFACT-IDENTICAL`

- [ ] **Step 3: Commit**

```bash
git add lib/kopsrox_artifacts.py lib/kopsrox_proxmox.py
git commit -m 'reimpl: artifacts + proxmox explicit imports, pep8, hints'
```

---

### Task 6: kopsrox_k3s.py — explicit imports

**Files:**
- Modify: `lib/kopsrox_k3s.py`

**Interfaces:**
- Produces (unchanged names, typed): `k3s_check(vmid: int) -> bool`, `k3s_init_node(vmid: int = ..., nodetype: str = 'master', snapshot: str = 'kopsrox') -> None`, `k3s_remove_node(vmid: int) -> None`, `k3s_rm_cluster() -> None`, `cluster_plan_total() -> int`, `k3s_update_cluster() -> None`, `kubeconfig() -> None`, `kubectl(cmd: str) -> str`, `k3s_check_config() -> None`, `export_k3s_token() -> None`, `cluster_info() -> None`, `reload_kubevip() -> str`, `get_kube_vip_master() -> str`.
- Consumes: Task 5 proxmox functions + Task 4 config attributes, all imported explicitly.

- [ ] **Step 1: Convert** — same method as Task 5 Step 1.

- [ ] **Step 2: Verify**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, 'lib')
import kopsrox_config as c
c.init('cluster', 'info')
import kopsrox_k3s
print('master check:', kopsrox_k3s.k3s_check(c.masterid) if c.masterid in c.vms else 'no cluster - import ok')
"
```
Expected: `True` when a cluster is live, otherwise `no cluster - import ok`; exit 0 either way.

- [ ] **Step 3: Commit**

```bash
git add lib/kopsrox_k3s.py
git commit -m 'reimpl: k3s explicit imports, pep8, hints'
```

---

### Task 7: verb modules — run(cmd, arg) functions

**Files:**
- Modify: `lib/verb_image.py`, `lib/verb_cluster.py`, `lib/verb_k3s.py`, `lib/verb_etcd.py`, `lib/verb_node.py`

**Interfaces:**
- Produces: each module exposes `run(cmd: str, arg: str | None = None) -> None`. Task 8's dispatcher calls exactly this.
- Consumes: Tasks 4-6 names, imported explicitly.

- [ ] **Step 1: Convert each verb**

Pattern (verb_k3s shown in full as the model — apply the same shape to all five):

```python
#!/usr/bin/env python3

from kopsrox_config import cluster_name, masterid
from kopsrox_k3s import (
    export_k3s_token,
    k3s_check_config,
    kubeconfig,
    kubectl,
    reload_kubevip,
)
from kopsrox_kmsg import kmsg


def run(cmd: str, arg: str | None = None) -> None:
    if cmd == 'export-token':
        ...  # today's top-level block for this command, re-indented
    if cmd == 'kubeconfig':
        ...
    if cmd == 'check-config':
        ...
    if cmd == 'kubectl':
        ...
    if cmd == 'reload-kubevip':
        ...
```

Rules:
- `cmd = sys.argv[2]` / `sys.argv[3]` reads are deleted; `arg` replaces argv[3] (verb_node hostname, k3s kubectl command string, etcd restore snapshot name).
- Command bodies longer than ~10 lines become named module functions called from `run()` (e.g. `image_create() -> None`, `image_destroy() -> None`, `node_terminal(vmid: int) -> None`); short ones stay inline. `patch_microvm_template() -> str` stays a module function.
- verb_etcd's module-level prologue (master check, token check, s3 connection test, `snapshots` global) runs for every etcd command today — move it into a `_etcd_checks() -> str` helper returning the snapshot list, called at the top of `run()`; message order must stay identical.
- All `exit(0)` calls inside command bodies stay (they end the CLI, that is their job).
- Message strings and kname values byte-identical.

- [ ] **Step 2: Verify all five compile and export run**

Run: `python3 -c "import sys; sys.path.insert(0,'lib'); import ast; [print(f, any(isinstance(n, ast.FunctionDef) and n.name=='run' for n in ast.parse(open(f).read()).body)) for f in __import__('glob').glob('lib/verb_*.py')]"`
Expected: five lines, all `True`. Also `python3 -m py_compile lib/verb_*.py`.

- [ ] **Step 3: Commit**

```bash
git add lib/verb_*.py
git commit -m 'reimpl: verbs as run(cmd, arg) functions'
```

---

### Task 8: kopsrox.py dispatcher + dev test updates + full acceptance

**Files:**
- Modify: `kopsrox.py`, `dev/test_config.py`, `dev/test_kmsg.py` (only if either fakes argv — check), `dev/gen_config.sh` (only if it relies on removed behavior — check)

**Interfaces:**
- Consumes: `kopsrox_config.init(verb, cmd)` (Task 4), `verb_<verb>.run(cmd, arg)` (Task 7).

- [ ] **Step 1: Rewrite the tail of kopsrox.py**

Keep the `cmds` dict, help functions, and argv validation exactly as today (they were just fixed for exit codes). Replace only the final dispatch:

```python
# argument for commands that take one ( validated above )
arg = sys.argv[3] if len(sys.argv) > 3 else None

# staged config checks, then dispatch
import kopsrox_config
kopsrox_config.init(verb, cmd)
run_verb = __import__('verb_' + verb)
run_verb.run(cmd, arg)
```

Reformat the whole file to 4-space while there.

- [ ] **Step 2: Full acceptance against baselines**

```bash
set -e
B=.baseline
check() {
    local name=$1; shift
    "$@" > /tmp/reimpl-$name.txt 2>&1; echo $? > /tmp/reimpl-$name.exit
    diff /tmp/reimpl-$name.txt $B/$name.txt
    diff /tmp/reimpl-$name.exit $B/$name.exit
    echo "$name OK"
}
check help            ./kopsrox.py
check help-cluster    ./kopsrox.py cluster
check help-image      ./kopsrox.py image
check help-etcd       ./kopsrox.py etcd
check help-k3s        ./kopsrox.py k3s
check help-node       ./kopsrox.py node
check bad-verb        ./kopsrox.py bogus
check bad-cmd         ./kopsrox.py etcd restore-latest
check missing-arg     ./kopsrox.py etcd restore
check image-info      ./kopsrox.py image info
check cluster-info    ./kopsrox.py cluster info
./dev/gen_config.sh && diff kopsrox.ini.default $B/default.ini && echo INI-OK
./dev/test_config.py && ./dev/test_kmsg.py | cat
```
Expected: every line `OK`, `INI-OK`, both suites pass. If `cluster-info` or `image-info` diffs are solely because the live environment changed since Task 1 (cluster created/destroyed, image rebuilt), re-capture just that baseline on the PRE-CHANGE code via `git stash` ... `git stash pop` and re-verify — note it in the report.

- [ ] **Step 3: Update dev tests if they fake argv**

`dev/test_config.py` imports only `kopsrox_schema` (no change expected). Grep both dev tests for `sys.argv` hacks importing `kopsrox_config` or `kopsrox_artifacts`; replace any with `kopsrox_config.init(...)`. `dev/capture_baseline.sh`'s argv-hack block is now dead — delete the script (baselines stay on disk, gitignored).

- [ ] **Step 4: Commit**

```bash
git add kopsrox.py dev/
git rm dev/capture_baseline.sh
git commit -m 'reimpl: explicit dispatch - init(verb, cmd) + run(cmd, arg)'
```

---

### Task 9: sweep + CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (architecture section)
- Verify-only: whole tree

- [ ] **Step 1: Mechanical sweeps**

```bash
grep -rn 'import \*' lib/ kopsrox.py && echo FAIL || echo 'no star imports'
grep -rn 'sys.argv' lib/ && echo FAIL || echo 'no argv in lib'
grep -rn $'\t' lib/*.py kopsrox.py && echo FAIL || echo 'no tabs'
grep -rnE '^\s{2}[^ ]' lib/kopsrox_config.py | head -3   # eyeball: no 2-space indent remains
grep -rn 'except:' lib/ kopsrox.py                        # eyeball: every hit must be except Exception or narrower
```

- [ ] **Step 2: Rewrite CLAUDE.md architecture bullets 1-3**

Describe: argv validation in kopsrox.py → `kopsrox_config.init(verb, cmd)` staged checks (same stage list as today's text) → `verb_<verb>.run(cmd, arg)`; explicit imports; config importable outside the CLI (drop the "cannot be imported outside the CLI entrypoint" caveat); everything else (schema single-source, kname conventions, module list) stays accurate — re-read each architecture bullet against the new code and fix any that lie.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m 'reimpl: CLAUDE.md architecture update'
```

---

### Final gate (controller, not a subagent task)

Whole-branch review, then ask the user for permission to run `dev/rls_test.sh`
(destructive: rebuilds image + cluster on the live host). Only merge/push after
the user's decision on that run.
