# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

kopsrox — a Python CLI that creates and manages HA k3s clusters on Proxmox VE. It builds a cloud-image-based VM template, clones it into master/worker nodes, installs k3s via the Proxmox qemu-guest-agent (no SSH), and wires in kube-vip, the Proxmox cloud-controller-manager, and the Proxmox CSI driver. This checkout ("anchovy") is a working copy: `kopsrox.ini` here points at a live Proxmox host with real credentials, and most commands mutate that live environment.

## Running it

```
./kopsrox.py [verb] [command] [arg]
```

Verbs and their commands are declared in the `cmds` dict at the top of `kopsrox.py` — that dict is the single source of truth for the CLI surface. Running with no args prints help; running with no `kopsrox.ini` present generates a default one (from `lib/kopsrox_ini.py`) and exits.

- Python deps: `proxmoxer`, `requests`, `wget`, `termcolor`, `urllib3` (no requirements.txt — install manually).
- Host deps: `virt-customize` (libguestfs) and `qm` via sudo, used during `image create`.
- There is no build step, linter, or unit test suite. `dev/testing.sh` is a manual integration test that creates/destroys real clusters against the configured Proxmox host — treat it as destructive.
- `dev/kubectl.sh` wraps kubectl with the exported `<cluster_name>.kubeconfig`; other `dev/*.sh` scripts are similar one-liner conveniences.
- `dev/gen_config.sh` regenerates the default ini as `kopsrox.ini.default` without clobbering the real config.

## Architecture

Execution is driven by import side effects, not function calls:

1. `kopsrox.py` validates argv against the `cmds` dict, then runs `__import__('verb_' + verb)`. Each `lib/verb_*.py` executes top-to-bottom on import, reading `sys.argv[2]`/`sys.argv[3]` itself.
2. Import chain: `verb_*` → `kopsrox_k3s` → `kopsrox_proxmox` → `kopsrox_config`, each doing `from x import *`. Everything ultimately shares module-level globals defined in `lib/kopsrox_config.py`.
3. `lib/kopsrox_config.py` runs at import time: parses `kopsrox.ini`, validates every setting (`conf_check`), opens the Proxmox API connection (`prox`), verifies node/storage/bridge, and even powers on any stopped cluster VMs. Adding a config option means adding a `conf_check()` call here plus a default in `kopsrox_ini.py`. It also reads `sys.argv[1]` directly, so these modules cannot be imported outside the CLI entrypoint.

Key modules:

- `lib/kopsrox_proxmox.py` — Proxmox primitives: `qa_exec()` (run a command inside a VM via qemu-guest-agent, polling until exit — this is how ALL in-VM work happens), `clone()`, `prox_destroy()`, `prox_task()` (blocks until a Proxmox task finishes and checks exit status).
- `lib/kopsrox_k3s.py` — cluster logic: `k3s_init_node()` (master/slave/worker/restore), `k3s_update_cluster()` (reconciles running VMs against `masters`/`workers` in the ini, adding or draining+removing nodes), kubeconfig/token export, `kubectl()` (runs kubectl on the master via qa_exec).
- `lib/verb_image.py` — `image create` downloads the cloud image, generates all cluster artifacts from config (kubevip/traefik/CCM/CSI manifests into `lib/manifests/kopsrox-<cluster>.yaml`, the k3s server `config.yaml`, and the in-VM install script `lib/scripts/kopsrox.sh`), bakes them into the image with `virt-customize`, and imports it as Proxmox template VM `cluster_id`. These generated files are gitignored — never edit `lib/scripts/kopsrox.sh` or `lib/manifests/config.yaml` directly; change the f-string templates in `verb_image.py` and rebuild the image.
- `lib/kopsrox_kmsg.py` — all user output goes through `kmsg(kname, msg, sev)`. `kname` is `scope_action` (split on the first `_` for coloring); `sev` is `info` (default), `sys`, or `err`.

VM ID / naming convention (defined by `suffixes`/`vmnames` in `kopsrox_config.py`): `cluster_id` = template `-i0`, `+1..+3` = masters `-m1..-m3` (m1's ID is `masterid`), `+4` = utility `-u1`, `+5..+9` = workers `-w1..-w5`. Each VM's IP is derived arithmetically: `network_ip`'s last octet + (vmid − cluster_id). `network_ip` itself is the kube-vip VIP. Only 1 or 3 masters are supported.

Error-handling convention: broad `try/except` with `kmsg(..., 'err')` followed by `exit(0)` — errors deliberately exit with status 0, and control flow frequently relies on `except` around attribute/index access.

## Cluster artifacts in repo root

`<cluster_name>.kubeconfig` and `<cluster_name>.k3stoken` are exported per cluster and are required for restore operations (`etcd restore` compares the saved token's password against the live one). Etcd snapshots go to the S3 endpoint configured in the ini; `cluster restore` rebuilds the whole cluster from the latest snapshot.


## General

Never commit as Claude always use the details im users .gitconfig
