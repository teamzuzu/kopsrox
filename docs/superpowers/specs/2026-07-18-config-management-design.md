# Config Management + Check Optimisation Design

Date: 2026-07-18
Status: approved (design discussion in session; this document is the written spec)

## Problem

- Config options are defined in two places: 32 `conf_check()` calls in `lib/kopsrox_config.py` and a parallel hand-written block of defaults + comments in `lib/kopsrox_ini.py`. Adding an option means editing both (documented in CLAUDE.md as such).
- Every kopsrox command pays 6 + one-per-VM Proxmox API round trips at import time (cluster status, node list, k3s exec probe, storage check, image content list, resources list, `status.current` per VM), plus side effects that don't belong on every command.
- The master-up probe fires `k3s kubectl version` in the guest via `agent.exec.post` and never polls it — every kopsrox invocation leaves a stray process in m1.
- `if vm_disk < 1` under the vm_cpu comment — the vm_cpu floor is never actually checked (latent bug).

## Decisions (made with user)

1. **Flat globals stay.** Consumers keep `from kopsrox_config import *` and the same ~30 names (`cluster_name`, `masterid`, `prox`, `vms`, ...). All restructuring is internal to the config side.
2. **All four check optimisations approved:** merge redundant API calls into one discovery call; replace the k3s exec probe with an agent ping; scope checks per verb; move VM power-on out of unconditional import.
3. **One schema** drives both validation and the generated default ini (defaults + comments), so adding an option = one edit.

## Non-goals

- No `cfg.` namespace object; no changes to the import-side-effect architecture.
- No changes to verb logic beyond what check-scoping requires. `list_kopsrox_vm()` remains a live query where verbs re-poll it.
- No new dependencies.

## Module layout

### `lib/kopsrox_schema.py` (new, pure)

No side effects, no proxmoxer, no argv, stdlib + `kopsrox_kmsg` only. This module MUST NOT import `kopsrox_config` — it is imported by `kopsrox_ini.py`, which runs precisely when `kopsrox.ini` does not exist yet (config would abort).

Contents:

- `SCHEMA` — ordered list of option entries: `{'name', 'comment', 'default', 'kind' (str|int), 'blank_ok' (bool), 'commented' (bool), 'check' (callable|None)}`. Entries appear in the same order as today's generated ini. `commented=True` marks options shipped commented out (`microvm_kernel`, `microvm_initrd`, `s3_region`).
- Named validators (each calls `kabort('..._config-check', <today's exact message>)` on failure):
  - `cluster_id` ≥ 100
  - `proxmox_endpoint` not localhost/127.0.0.1
  - `vm_disk` ≥ 20
  - `vm_cpu` ≥ 1 (fixes the `vm_disk < 1` bug — the old check never ran against vm_cpu)
  - `vm_ram` ≥ 2
  - `cloudinitsshkey` starts with `ssh-`
  - `masters` in (1, 3)
- `validate(configparser) -> dict` — resolves `cluster_name` first so abort messages carry today's kname (`config_check` until cluster_name is known, `<cluster_name>_config-check` after). Then for each non-commented entry: present (else abort), non-blank unless `blank_ok` (else abort), int-coerced when `kind=int` (else abort), `check` run if set. Commented entries resolve via `get(..., fallback=default)`. Returns `{name: value}`.
- `render_ini() -> ConfigParser` — builds the default-ini ConfigParser from SCHEMA using the same `allow_no_value` comment technique as today (`; comment` rows, `# name = value` rows for commented entries), so the generated file format is unchanged.

Option table (defaults verbatim from today's `kopsrox_ini.py`; kind=int for `proxmox_api_port`, `vm_cpu`, `vm_ram`, `vm_disk`, `cluster_id`, `workers`, `masters`, `network_mtu`; blank_ok for `extra_packages`, `s3_region`):

proxmox_endpoint, proxmox_api_port, proxmox_user, proxmox_token_name, proxmox_token_value, proxmox_node, proxmox_storage, oci_image, microvm_kernel (commented), microvm_initrd (commented), extra_packages, vm_disk, vm_cpu, vm_ram, cloudinituser, cloudinitpass, cloudinitsshkey, network_bridge, network_ip, network_mask, network_gw, network_dns, network_mtu, cluster_id, cluster_name, masters, workers, k3s_version, s3_endpoint, s3_region (commented), s3_access-key, s3_access-secret, s3_bucket.

Minor behavior change (accepted): every option is validated on every command. Previously `oci_image` was only checked for image verbs — a missing/blank `oci_image` now aborts any command. This makes ini completeness uniform.

### `lib/kopsrox_config.py` (rewritten internals, same exports)

Still executes at import; same global names exported. Order:

1. **Parse + validate (no network).** Read `kopsrox.ini`, `globals().update(kopsrox_schema.validate(...))`, then compute derived values exactly as now: `masterid`, `network_octs/base/ip_prefix`, `region_string`, `suffixes`, `vmnames`.
2. **Connect.** Open `ProxmoxAPI` and verify with `prox.cluster.status.get()` (one call, unchanged error message via `kabort`).
3. **Discover (one call).** `prox.cluster.resources.get()` provides everything the old code made 4 + n calls for:
   - node check: entries with `type == 'node'` → `disc_nodes`; abort if `proxmox_node` missing (today's message).
   - storage check: an entry with `type == 'storage'`, `node == proxmox_node`, `storage == proxmox_storage`; abort if absent.
   - `vms` dict: `type == 'qemu'` entries with `cluster_id <= vmid < cluster_id + 10` → `{vmid: node}` (sorted), and their `status` values retained for stage 4.
   - image presence: vmid `cluster_id` present (the template is a qemu resource). Expectation skipped for `image create`, as now; abort message unchanged. `kopsrox_img()` (storage content lookup) remains as a function for `image destroy`/`image info` display but is no longer called at import.
   - `list_kopsrox_vm()` keeps its current live implementation (`cluster.resources.get`) for verbs that re-poll.
4. **Verb-scoped stages**, keyed off `passed_cmd = sys.argv[1]`:
   - `image`: dpkg pve-microvm version floor (unchanged), upstream release notice (unchanged), `microvm_kernel/initrd` fallbacks, template description fetch (try/except, unchanged), bridge/SDN discovery gated by `not conf_check_master_up` (unchanged condition).
   - guest verbs (`cluster`, `k3s`, `etcd`, `node`): power on any VM whose discovered status is `stopped` (same start post, same message; derived from stage 3 — no per-VM `status.current` calls).
   - `conf_check_master_up`: computed for `image` and `cluster` verbs only (its two consumers: the bridge-check gate and `cluster_plan_total()`); defined `False` otherwise. Computed as: master vmid discovered running AND `agent.ping.post()` succeeds. This is an agent-alive proxy, not a k3s-alive probe; the one consumer where the distinction matters (`cluster_plan_total`'s +1 export unit) can be off by one plan unit when the agent is up but k3s isn't installed — the progress bar clamps, accepted. The stray-process wart from the old exec probe disappears.

Resulting Proxmox API calls at import: baseline **2** (connect check + discovery) for every command; `cluster` and `image` verbs add 1 agent ping when the master is discovered running; `image` verbs additionally keep their existing template-description fetch, bridge discovery (only when the master is not up), and the GitHub release notice; guest verbs add 1 start post per stopped VM. Down from 6 + one-per-VM on every command.

Behavior changes (all accepted in design discussion):

- `image info` / `image destroy` no longer power on stopped cluster VMs; only guest verbs do.
- No in-guest exec at import, ever.
- Missing `oci_image` aborts all commands, not just image ones.
- vm_cpu floor actually enforced.

### `lib/kopsrox_ini.py` (renderer wrapper)

`init_kopsrox_ini()` becomes: build parser via `kopsrox_schema.render_ini()`, write `kopsrox.ini`, print today's message. `dev/gen_config.sh` continues to work unchanged.

## Error handling

All failures via `kabort` (exit 1) with today's message text. `kname` stays `config_check` before the cluster name is known and `<cluster_name>_config-check` after, matching current output.

## Testing

- **New `dev/test_config.py`** (same self-contained style as `dev/test_kmsg.py`, no Proxmox needed):
  - render: `render_ini()` output parses back and contains every non-commented SCHEMA name; commented names appear as `# name` lines.
  - round-trip: every SCHEMA default passes `validate()` (defaults must be self-consistent).
  - negatives, in-process by catching `SystemExit` and asserting `code == 1`: missing required option; blank required option; non-numeric int option; `masters = 2`; `cluster_id = 99`; localhost endpoint; bad ssh key; `vm_cpu = 0` (the fixed check).
- **Live verification:** `./kopsrox.py cluster info` and `./kopsrox.py image info` render as before; `time` comparison before/after to confirm the startup win; `./kopsrox.py cluster info | cat` stays ANSI-free.
- **Ini compatibility:** generate the default ini pre- and post-change and diff — comments and values must match (line ordering preserved by the ordered SCHEMA).

## Docs

CLAUDE.md: "Adding a config option means adding a `conf_check()` call here plus a default in `kopsrox_ini.py`" becomes "Adding a config option means adding one SCHEMA entry in `lib/kopsrox_schema.py`"; note the staged checks and the per-verb scoping in the architecture section.
