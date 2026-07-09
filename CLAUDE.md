# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

kopsrox — a Python CLI that creates and manages HA k3s clusters on Proxmox VE using pve-microvm microvms (https://github.com/rcarmo/pve-microvm). It builds an OCI-image-based microvm template via a patched copy of `pve-microvm-template`, clones it into master/worker nodes, configures each node and installs k3s via the Proxmox qemu-guest-agent (no SSH, no cloud-init), and wires in kube-vip and the k3s local-path provisioner. This checkout ("anchovy") is a working copy: `kopsrox.ini` here points at a live Proxmox host with real credentials, and most commands mutate that live environment.

No Proxmox cloud-controller-manager or CSI driver on microvm: the guest has no DMI so the CCM's SMBIOS-UUID check can never pass (kubelet's `--provider-id` flag covers providerID instead), and the CSI driver needs disk hotplug, which pve-microvm doesn't deliver into running guests (QEMU-level hot-add works behind cold-plugged pcie-root-ports, but pve-microvm's hotplug path only writes config — re-adding CSI would need a pve-microvm patch plus pciehp/udev in the guest).

## Running it

```
./kopsrox.py [verb] [command] [arg]
```

Verbs and their commands are declared in the `cmds` dict at the top of `kopsrox.py` — that dict is the single source of truth for the CLI surface. Running with no args prints help; running with no `kopsrox.ini` present generates a default one (from `lib/kopsrox_ini.py`) and exits.

- Python deps: `proxmoxer`, `requests`, `termcolor`, `urllib3` (no requirements.txt — install manually).
- Host deps: the pve-microvm .deb installed on the Proxmox node, the kopsrox microvm kernel (one-time build via `dev/build-kopsrox-kernel.sh` — the stock pve-microvm kernel lacks VXLAN/ipset/xt_* that k3s needs), and `qm`/`pve-microvm-template` via sudo during `image create`.
- There is no build step, linter, or unit test suite. `dev/testing.sh` is a manual integration test that creates/destroys real clusters against the configured Proxmox host — treat it as destructive.
- `dev/kubectl.sh` wraps kubectl with the exported `<cluster_name>.kubeconfig`; other `dev/*.sh` scripts are similar one-liner conveniences.
- `dev/gen_config.sh` regenerates the default ini as `kopsrox.ini.default` without clobbering the real config.

## Architecture

Execution is driven by import side effects, not function calls:

1. `kopsrox.py` validates argv against the `cmds` dict, then runs `__import__('verb_' + verb)`. Each `lib/verb_*.py` executes top-to-bottom on import, reading `sys.argv[2]`/`sys.argv[3]` itself.
2. Import chain: `verb_*` → `kopsrox_k3s` → `kopsrox_proxmox` → `kopsrox_config`, each doing `from x import *`. Everything ultimately shares module-level globals defined in `lib/kopsrox_config.py`.
3. `lib/kopsrox_config.py` runs at import time: parses `kopsrox.ini`, validates every setting (`conf_check`), opens the Proxmox API connection (`prox`), verifies node/storage/bridge, and even powers on any stopped cluster VMs. Adding a config option means adding a `conf_check()` call here plus a default in `kopsrox_ini.py`. It also reads `sys.argv[1]` directly, so these modules cannot be imported outside the CLI entrypoint.

Key modules:

- `lib/kopsrox_proxmox.py` — Proxmox primitives: `qa_exec()` (run a command inside a VM via qemu-guest-agent, polling until exit — this is how ALL in-VM work happens; the agent runs over virtio-serial, so it works before networking is up), `qa_write()` (push a file into a VM via the agent file-write API, chunked above 40KiB), `node_prepare()` (post-clone node identity: disables pve-microvm first-boot services, writes static-IP networkd config + iproute2 fallback unit + resolv.conf + hostname, wipes machine-id, pushes k3s scripts — plus `config.yaml`/manifests on masters only — grows the root fs, creates the user, then reboots and verifies IP/internet; idempotent via `/etc/kopsrox-node-init-done`), `node_reboot_wait()`, `clone()`, `prox_destroy()`, `prox_task()` (blocks until a Proxmox task finishes and checks exit status).
- `lib/kopsrox_k3s.py` — cluster logic: `k3s_init_node()` (master/slave/worker/restore), `k3s_update_cluster()` (reconciles running VMs against `masters`/`workers` in the ini, adding or draining+removing nodes), kubeconfig/token export, `kubectl()` (runs kubectl on the master via qa_exec).
- `lib/kopsrox_artifacts.py` — generator functions for all cluster artifacts (kubevip/traefik manifest, k3s server `config.yaml`, the in-VM install script `kopsrox.sh`). Called by `verb_image.py` (writes inspection copies under `lib/manifests/` and `lib/scripts/` — gitignored/generated, never edit them directly) and by `node_prepare()` (pushes config-fresh copies into nodes, so artifact changes take effect on node create without an image rebuild).
- `lib/verb_image.py` — `image create` builds a cluster-generic microvm template: writes a patched copy of `pve-microvm-template` to `lib/scripts/microvm-template.sh` (upstream's chroot install fails silently on ubuntu's empty `/etc/resolv.conf`, and its first-boot installer would block the guest agent behind a network that doesn't exist yet — each patch is asserted so upstream changes fail loudly), runs it, verifies the rootfs actually has systemd + qemu-ga by ro-mounting the template disk, then `sudo qm set --args` to boot the kopsrox kernel (`args` is root-only, so shell not API). Nothing cluster-specific is baked into the image — everything is delivered by `node_prepare()` at clone time.
- `lib/kopsrox_kmsg.py` — all user output goes through `kmsg(kname, msg, sev)`. `kname` is `scope_action` (split on the first `_` for coloring); `sev` is `info` (default), `sys`, or `err`.

VM ID / naming convention (defined by `suffixes`/`vmnames` in `kopsrox_config.py`): `cluster_id` = template `-i0`, `+1..+3` = masters `-m1..-m3` (m1's ID is `masterid`), `+4` = utility `-u1`, `+5..+9` = workers `-w1..-w5`. Each VM's IP is derived arithmetically: `network_ip`'s last octet + (vmid − cluster_id). `network_ip` itself is the kube-vip VIP. Only 1 or 3 masters are supported.

Microvm specifics: `machine: microvm` with direct kernel boot — no BIOS/UEFI, no cloud-init drive, no `boot=order`, no udev in the guest. Static IPs/MTU/DNS/users are all applied in-guest by `node_prepare()` via the agent (`ipconfig0`/`nameserver`/`net0 mtu=` are no-ops for microvms). The guest kernel is custom-built (`dev/build-kopsrox-kernel.sh` + `lib/scripts/kopsrox-kernel.config`) because guests have no `/lib/modules` — every feature k3s needs must be `=y`; note the kopsrox kernel's built-in `CONFIG_DUMMY`/SIT create `dummy0`/`sit0`, so network config must match `Driver=virtio_net`, never `Type=ether`. `qm stop` is a hard power-cut (no ACPI): guest page-cache writes are lost, so never stop a node to "check" something that was just written inside it. `qm shutdown`/`qm reboot` leave the QEMU process hanging in `paused (shutdown)` even though the guest complies — pve-microvm omits the qmeventd socket that would reap it — so reboots go through `node_reboot_wait()` (in-guest `systemctl reboot`, QEMU resets in place) and a stuck VM is recovered with `qm stop` + `qm start`.

Error-handling convention: broad `try/except` with `kmsg(..., 'err')` followed by `exit(0)` — errors deliberately exit with status 0, and control flow frequently relies on `except` around attribute/index access.

## Cluster artifacts in repo root

`<cluster_name>.kubeconfig` and `<cluster_name>.k3stoken` are exported per cluster and are required for restore operations (`etcd restore` compares the saved token's password against the live one). Etcd snapshots go to the S3 endpoint configured in the ini; `cluster restore` rebuilds the whole cluster from the latest snapshot.


## General

Never commit as Claude always use the details im users .gitconfig
