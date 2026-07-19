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

- Python deps: `proxmoxer`, `requests`, `urllib3` (no requirements.txt — install manually).
- Host deps: pve-microvm ≥ 0.3.19 on the Proxmox node (`image` commands enforce this and print a notice when upstream has a newer release), the kopsrox microvm kernel (one-time build via `dev/build-kopsrox-kernel.sh` — the stock pve-microvm kernel lacks VXLAN/veth/netfilter/BPF bits k3s needs), and `qm`/`pve-microvm-template` via sudo during `image create`.
- There is no build step, linter, or unit test suite. `dev/testing.sh` is a manual integration test that creates/destroys real clusters against the configured Proxmox host — treat it as destructive.
- Releases: push a `v*` tag — `.github/workflows/release.yml` syntax-checks, packages a tarball, and creates the GitHub release with generated notes.
- `dev/kubectl.sh` wraps kubectl with the exported `<cluster_name>.kubeconfig`; other `dev/*.sh` scripts are similar one-liner conveniences.
- `dev/gen_config.sh` regenerates the default ini as `kopsrox.ini.default` without clobbering the real config.

## Architecture

Execution is driven by import side effects, not function calls:

1. `kopsrox.py` validates argv against the `cmds` dict, then runs `__import__('verb_' + verb)`. Each `lib/verb_*.py` executes top-to-bottom on import, reading `sys.argv[2]`/`sys.argv[3]` itself.
2. Import chain: `verb_*` → `kopsrox_k3s` → `kopsrox_proxmox` → `kopsrox_config`, each doing `from x import *`. Everything ultimately shares module-level globals defined in `lib/kopsrox_config.py`.
3. `lib/kopsrox_config.py` runs at import time in stages: validates `kopsrox.ini` against the SCHEMA in `lib/kopsrox_schema.py` (injecting every option as a module global), opens the Proxmox API connection (`prox`), then makes ONE `cluster.resources` call that covers the node/storage/VM/image checks. Side effects are verb-scoped: guest verbs (`cluster`/`k3s`/`etcd`/`node`) power on stopped VMs; `image`+`cluster` ping the master agent (`conf_check_master_up`); image-only checks (pve-microvm version, bridge/SDN) run under `image`. Adding a config option means adding ONE `opt(...)` entry to `SCHEMA` in `lib/kopsrox_schema.py` — it drives validation, the global name (`var=` for ini names with hyphens), and the generated default ini (comments included; `lib/kopsrox_ini.py` just renders it). `kopsrox_schema.py` is pure and must never import `kopsrox_config` (the default ini is generated exactly when `kopsrox.ini` is missing); `kopsrox_config.py` reads `sys.argv[1]` directly, so it cannot be imported outside the CLI entrypoint. `dev/test_config.py` tests schema/renderer without touching Proxmox.

Key modules:

- `lib/kopsrox_proxmox.py` — Proxmox primitives: `qa_exec()` (run a command inside a VM via qemu-guest-agent, polling until exit — this is how ALL in-VM work happens; the agent runs over virtio-serial, so it works before networking is up), `qa_write()` (push a file into a VM via the agent file-write API, chunked above 40KiB), `node_prepare()` (post-clone node identity: disables pve-microvm first-boot services, writes static-IP networkd config + iproute2 fallback unit + resolv.conf + hostname, wipes machine-id, pushes k3s scripts — plus `config.yaml`/manifests on masters only — grows the root fs, creates the user, then reboots and verifies IP/internet; idempotent via `/etc/kopsrox-node-init-done`), `node_reboot_wait()`, `clone()`, `prox_destroy()`, `prox_task()` (blocks until a Proxmox task finishes and checks exit status).
- `lib/kopsrox_k3s.py` — cluster logic: `k3s_init_node()` (master/slave/worker/restore), `k3s_update_cluster()` (reconciles running VMs against `masters`/`workers` in the ini, adding or draining+removing nodes), kubeconfig/token export, `kubectl()` (runs kubectl on the master via qa_exec). With no cloud controller, kopsrox owns node-object lifecycle itself: `k3s_remove_node()` deletes both the node object AND its `<node>.node-password.k3s` secret (k3s rejects a rebuilt node reusing the name otherwise), and `cluster restore` purges stale node objects/secrets from the restored datastore before rebuilding. `k3s_check()` matches `\bReady\b` deliberately — plain `Ready` also matches `NotReady` and once made restores skip installing k3s on rebuilt nodes entirely.
- `lib/kopsrox_artifacts.py` — generator functions for all cluster artifacts (kubevip/traefik manifest, k3s server `config.yaml`, the in-VM install script `kopsrox.sh`). Called by `verb_image.py` (writes inspection copies under `lib/manifests/` and `lib/scripts/` — gitignored/generated, never edit them directly) and by `node_prepare()` (pushes config-fresh copies into nodes, so artifact changes take effect on node create without an image rebuild).
- `lib/verb_image.py` — `image create` builds a cluster-generic microvm template: writes a patched copy of `pve-microvm-template` to `lib/scripts/microvm-template.sh` (upstream's chroot install fails silently on ubuntu's empty `/etc/resolv.conf`, and its first-boot installer would block the guest agent behind a network that doesn't exist yet — each patch is asserted so upstream changes fail loudly), runs it, verifies the rootfs actually has systemd + qemu-ga by ro-mounting the template disk, then `sudo qm set --args` to boot the kopsrox kernel (`args` is root-only, so shell not API). Nothing cluster-specific is baked into the image — everything is delivered by `node_prepare()` at clone time.
- `lib/kopsrox_kmsg.py` — the only module that emits ANSI; all user output goes through it. `kmsg(kname, msg, sev)` prints a glyph + severity-colored line (`kname` is `scope_action`, split on the first `_`; `sev` is `info`/`sys`/`err`); `kabort(kname, msg)` is `err` + `exit(1)`; `kstep(kname, msg)` is a context manager showing a live spinner with elapsed time (`quiet=True` for polling internals like `qa_exec`/`prox_task` — nothing printed on success); `kplan(add, title)`/`kplan_tick()` drive the overall `4/9` progress bar in compound verbs (`cluster create/update/restore/destroy`, `image create`). Output degrades to plain sequential lines when stdout is not a tty; `NO_COLOR` disables color. `dev/test_kmsg.py` tests the module standalone (scripted asserts when piped, visual demo on a tty).

VM ID / naming convention (defined by `suffixes`/`vmnames` in `kopsrox_config.py`): `cluster_id` = template `-i0`, `+1..+3` = masters `-m1..-m3` (m1's ID is `masterid`), `+4` = utility `-u1`, `+5..+9` = workers `-w1..-w5`. Each VM's IP is derived arithmetically: `network_ip`'s last octet + (vmid − cluster_id). `network_ip` itself is the kube-vip VIP. Only 1 or 3 masters are supported.

Microvm specifics and traps (each of these was learned the hard way):

- `machine: microvm` with direct kernel boot — no BIOS/UEFI, no cloud-init drive, no `boot=order`. Static IPs/MTU/DNS/users are all applied in-guest by `node_prepare()` via the agent (`ipconfig0`/`nameserver`/`net0 mtu=` are no-ops for microvms).
- The guest kernel is custom-built (`dev/build-kopsrox-kernel.sh` + `lib/scripts/kopsrox-kernel.config`) because guests have no `/lib/modules` — every feature k3s needs must be `=y`. When adding kernel options, audit the final `.config`: `olddefconfig` silently drops options with unmet dependencies (most `xt_*` need `NETFILTER_ADVANCED=y`).
- The kopsrox kernel's built-in DUMMY/SIT create `dummy0`/`sit0` in guests, so network matching must use `Driver=virtio_net`, never `Type=ether` (which once assigned the node IP to dummy0 and blackholed the default route).
- Guests boot in ~1s. The 90s-boot failure mode is udev missing from the image (device units never appear, `serial-getty` waits on `dev-ttyS0.device` forever) — the template installs udev/sudo/systemd-timesyncd on top of upstream's package set, plus `net.ifnames=0` on the kernel cmdline so udev doesn't rename `eth0` (kube-vip's manifest hardcodes it).
- `qm stop` is a hard power-cut (no ACPI): guest page-cache writes are lost, so never stop a node to "check" something that was just written inside it. Debug live — the serial console accepts input only with `\r` line endings (via socat on `/var/run/qemu-server/<vmid>.serial0`).
- `qm shutdown`/`qm reboot` need pve-microvm ≥ 0.3.19 (our upstream fix: qmeventd socket + dbus; #13/#14). kopsrox's own reboots use `node_reboot_wait()` (in-guest `systemctl reboot`, detected by boot-id change — microvms reboot too fast for agent-down polling). A VM stuck in `paused (shutdown)` recovers with `qm stop` + `qm start`.
- pvedaemon holds pve-microvm's perl code in memory: on pve-microvm < 0.3.20, `systemctl restart pvedaemon` after install/upgrade or API-started VMs (everything kopsrox starts) silently run the old code (0.3.20+ postinst restarts it itself).

Error-handling convention: errors go through `kabort(kname, msg)` — an `err` line then `exit(1)`; success paths exit 0. Broad `try/except` around attribute/index access is still common, but SystemExit-as-control-flow (an `exit()` inside `try` caught by a bare `except`) has been removed — don't reintroduce it.

## Cluster artifacts in repo root

`<cluster_name>.kubeconfig` and `<cluster_name>.k3stoken` are exported per cluster and are required for restore operations (`etcd restore` compares the saved token's password against the live one). Etcd snapshots go to the S3 endpoint configured in the ini; `cluster restore` rebuilds the whole cluster from the latest snapshot, purging stale node objects/password secrets before rebuilding workers.


## General

Never commit as Claude always use the details im users .gitconfig
