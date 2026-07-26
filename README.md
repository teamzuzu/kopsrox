# kopsrox

kopsrox creates and manages highly available [k3s](https://k3s.io) clusters on [Proxmox VE](https://www.proxmox.com/), using lightweight microvms from [pve-microvm](https://github.com/rcarmo/pve-microvm).

- Nodes are microvms built from a plain OCI image (default `ubuntu:24.04`) — no ISOs or cloud images required.
- Microvms boot in about one second; a fresh HA-capable cluster is ready in roughly 2.5 minutes.
- Master and worker nodes are added or removed by editing a single config file and running one command.
- [kube-vip](https://kube-vip.io/) is built in, providing a highly available VIP for the Kubernetes API and Traefik.
- Storage is provided by the k3s local-path provisioner.
- etcd snapshots can be pushed to and restored from S3-compatible storage with a single command.
- All node configuration happens through the QEMU guest agent — no cloud-init and no SSH required, and it works before the node has networking.
- The kubeconfig and k3s join token are exported automatically after every cluster operation.

Get the latest release: https://github.com/simonccc/kopsrox/releases

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Getting started](#getting-started)
- [Command reference](#command-reference)
- [FAQ](#faq)
- [Acknowledgements](#acknowledgements)

## Requirements

- A Proxmox VE host with root access, or a user that can `sudo` without a password — kopsrox runs directly on the Proxmox node.
- [pve-microvm](https://github.com/rcarmo/pve-microvm) v0.3.19 or later, installed on the node.
- A network with internet access, configured in Proxmox as a bridge or a Proxmox SDN network.
- A range of 10 free VM IDs (for example, 600–610).
- A range of 10 free IPs on that network (for example, 192.168.0.160–192.168.0.170).

## Installation

Install the Python dependencies:

```
sudo apt install python3-termcolor python3-proxmoxer python3-requests -y
```

Install pve-microvm — see the [pve-microvm installation docs](https://github.com/rcarmo/pve-microvm/blob/main/docs/installation.md):

```
curl -sLO $(curl -s https://api.github.com/repos/rcarmo/pve-microvm/releases/latest | grep browser_download_url | grep '.deb' | cut -d'"' -f4)
sudo dpkg -i pve-microvm_*.deb
sudo systemctl restart pvedaemon
```

> **Important:** always restart `pvedaemon` after installing or upgrading pve-microvm. It keeps the previous version loaded in memory, and VMs started via the API will silently run the old code until it is restarted.

Build the kopsrox microvm kernel. This is a one-time step — the stock pve-microvm kernel is missing features k3s requires, such as VXLAN and veth:

```
./dev/build-kopsrox-kernel.sh
```

This fetches [pve-microvm's kernel builder](https://github.com/rcarmo/pve-microvm/tree/main/kernel), merges in the k3s configuration fragment from `lib/scripts/kopsrox-kernel.config`, and installs `/usr/share/pve-microvm/vmlinuz-kopsrox` and `initrd-kopsrox`.

Generate an API token:

```
sudo pvesh create /access/users/root@pam/token/kopsrox
sudo pveum acl modify / --roles Administrator --user root@pam --token 'root@pam!kopsrox'
```

Keep the returned token value — it is needed in `kopsrox.ini`, described below.

## Configuration

Run `./kopsrox.py` once and a default `kopsrox.ini` will be generated. Edit it for your setup. Most values are self-explanatory; the following are worth understanding before your first run:

| Option | Purpose |
| --- | --- |
| `oci_image` | The OCI image nodes are built from. The default `ubuntu:24.04` is well-tested; other apt-based images should work but are untested. |
| `localuser` / `localpass` / `localsshkey` | The user baked into the image at `image create`/`update` time. There is no cloud-init on microvm, so this is how kopsrox provisions login access - changing these values needs an `image update` to take effect. |
| `network_mtu` | Applied inside each node. Set to 1450 when using a Proxmox SDN network. |
| `s3_*` | S3-compatible credentials for etcd snapshots. Works with Cloudflare R2, Backblaze B2, MinIO, and similar providers. |

### VM ID and IP layout

kopsrox uses a static ID/IP scheme derived from `cluster_id` and `network_ip`. For example:

```
network_ip = 192.168.0.170
cluster_id = 620
cluster_name = kopsrox
```

produces this layout:

| # | VM ID | IP | Role | Hostname |
| --- | --- | --- | --- | --- |
| 0 | 620 | 192.168.0.170 | image / VIP | kopsrox-i0 |
| 1 | 621 | 192.168.0.171 | master 1 | kopsrox-m1 |
| 2 | 622 | 192.168.0.172 | master 2 | kopsrox-m2 |
| 3 | 623 | 192.168.0.173 | master 3 | kopsrox-m3 |
| 4 | 624 | 192.168.0.174 | utility | kopsrox-u1 |
| 5 | 625 | 192.168.0.175 | worker 1 | kopsrox-w1 |
| 6 | 626 | 192.168.0.176 | worker 2 | kopsrox-w2 |
| 7 | 627 | 192.168.0.177 | worker 3 | kopsrox-w3 |
| 8 | 628 | 192.168.0.178 | worker 4 | kopsrox-w4 |
| 9 | 629 | 192.168.0.179 | worker 5 | kopsrox-w5 |

`network_ip` itself (here, 192.168.0.170) is the kube-vip VIP — a highly available address for the Kubernetes API and Traefik when running 3 masters.

## Getting started

With `kopsrox.ini` configured, a cluster is three commands away.

**Build the image.** Builds a microvm template from the configured OCI image with k3s already baked in (takes a few minutes — watch `kopsrox-image.log` if curious):

```
./kopsrox.py image create
```

**Create the cluster.** Clones the template into master and worker nodes, installs k3s, and exports `<cluster_name>.kubeconfig` and `<cluster_name>.k3stoken` to the current directory. Around 2.5 minutes for 1 master and 1 worker:

```
./kopsrox.py cluster create
```

**Use it:**

```
./kopsrox.py cluster info
./kopsrox.py k3s kubectl get pods -A
kubectl --kubeconfig=<cluster_name>.kubeconfig get nodes
```

**Scale it.** Edit `kopsrox.ini` — for example, set `workers = 3` or `masters = 3` (1 or 3 masters are supported) — then:

```
./kopsrox.py cluster update
```

With 3 masters, the Kubernetes API remains available even if the node holding the VIP is lost.

**Back it up.** Configure the `s3_*` settings in `kopsrox.ini` for your provider, then:

```
./kopsrox.py etcd snapshot
./kopsrox.py etcd list
```

**Restore it.** Rebuilds the whole cluster from the latest S3 snapshot, even if every node is gone:

```
./kopsrox.py cluster restore
```

## Command reference

`./kopsrox.py [verb] [command] [arg]`

### image

- **create / update** — builds a cluster-generic microvm template from the OCI image set in `kopsrox.ini`, using a patched copy of [pve-microvm-template](https://github.com/rcarmo/pve-microvm) (log in `kopsrox-image.log`). Verifies the rootfs, sets the kopsrox kernel, boots the template once to bake in everything that's identical across every node - `k3s_version` (both the master/slave and worker systemd services, ready but not started), the `localuser` account, `network_dns`, `extra_packages`, and the kube-vip/traefik manifest - then converts the VM to a template on VM ID `cluster_id`. Only genuinely per-node state (static IP, hostname, machine-id, root fs resize) is applied at clone time via the guest agent. Changing any of the values above takes effect on the next `image update`, not on the next node join.
- **info** — prints the template description (source image, k3s version, creation time) and its storage volume.
- **destroy** — deletes the image template.

### cluster

- **create** — creates a fresh cluster: clones the template into master (and worker) nodes per `kopsrox.ini`, installs k3s, and exports the kubeconfig and token. Safe to re-run — if a working master already exists, it behaves like `cluster update`.
- **update** — reconciles the running cluster against `masters` / `workers` in `kopsrox.ini`, adding or draining and removing nodes as needed.
- **info** — lists VM IDs, hostnames, IPs, and which Proxmox host each node runs on, plus `kubectl get nodes`. Shows which node currently holds the VIP.
- **restore** — rebuilds the whole cluster from the latest S3 etcd snapshot, even if all nodes are gone. Restores the master, then reconciles the rest per `kopsrox.ini`.
- **destroy** — destroys the cluster immediately, with no confirmation prompt. Workers are drained and removed first, then masters. The image template and utility node are left alone.

### k3s

- **export-token** — exports the cluster's k3s token to `<cluster_name>.k3stoken`. Required for restores; keep it safe.
- **kubeconfig** — exports the kubeconfig to `<cluster_name>.kubeconfig`, patched to point at the VIP instead of `127.0.0.1`.
- **check-config** — runs `k3s check-config` on the master and prints the output. The kopsrox kernel exposes `/proc/config.gz`, so this runs fully.
- **kubectl [cmd]** — runs kubectl on the master via the guest agent, for example: `./kopsrox.py k3s kubectl get events -A`.
- **reload-kubevip** — restarts the kube-vip daemonset.

### etcd

- **snapshot** — takes an etcd snapshot and uploads it to the configured S3 storage.
- **list** — lists this cluster's snapshots in S3.
- **restore [snapshot]** — restores the cluster from a specific snapshot (names come from `etcd list`). To restore the latest snapshot, use `cluster restore` instead.
- **prune** — deletes old snapshots according to the retention policy.

### node

- **utility** — creates a spare "utility" node (`-u1`): fully configured but without k3s installed, useful for testing and debugging.
- **terminal [hostname]** — connects to the node's serial console via `qm terminal`, with root autologin and no password required (Ctrl-O to exit).
- **ssh [hostname]** — connects to a node over SSH as the configured user (requires your SSH key in `kopsrox.ini`).
- **reboot [hostname]** — reboots the node via the guest agent and waits for it to come back (microvms reboot in about a second).
- **destroy [hostname]** — drains, removes, and deletes the node immediately, with no confirmation prompt.
- **k3s-uninstall [hostname]** — uninstalls k3s from the node, useful for experimenting with reinstalls.
- **rejoin-slave [hostname]** — reinstalls k3s on a master and rejoins it to the cluster.
- **cluster-exec [command]** — runs a command on every node via the guest agent.

## FAQ

**Why microvms instead of full VMs?**

Microvms (via [pve-microvm](https://github.com/rcarmo/pve-microvm)) skip BIOS/UEFI, PCI enumeration, and most emulated hardware. A node boots in about one second, and a whole cluster builds in roughly 2.5 minutes — with the same KVM isolation as a full VM, at a fraction of the overhead.

**Why does kopsrox need its own kernel?**

Microvm guests boot a kernel supplied by the host and have no `/lib/modules`, so everything k3s needs (VXLAN for Flannel, veth, netfilter, ipset, BPF for cgroup v2, and more) must be compiled in. The stock pve-microvm kernel doesn't include these, so `dev/build-kopsrox-kernel.sh` builds one that does — a one-time step that takes roughly 10 minutes.

**What happened to the Proxmox CSI driver and cloud controller manager?**

Neither works on microvm. The guest has no DMI/SMBIOS, so the cloud controller's identity check can never pass, and the CSI driver needs disk hotplug into running guests, which pve-microvm doesn't support yet. kopsrox sets the node `--provider-id` directly and uses the k3s local-path provisioner for storage instead. NFS also works fine (`extra_packages = nfs-common` is the default, and the kernel has the NFS client built in).

**`qm shutdown` / `qm reboot` don't work from the Proxmox UI.**

They do, as of pve-microvm v0.3.19 (a fix kopsrox contributed upstream). Remember to `systemctl restart pvedaemon` after any pve-microvm install or upgrade, or VMs started via the API will keep running the old code.

**Be careful with `qm stop`.**

`qm stop` on a microvm is a hard power cut — there is no ACPI power button — so anything the guest hasn't synced to disk is lost. Use `./kopsrox.py node reboot` or `qm shutdown` for graceful operations instead.

**Can I use a different OCI image than Ubuntu?**

Set `oci_image` in `kopsrox.ini`. Other apt-based images (for example, `debian:trixie-slim`) should work but are untested with kopsrox. The kernel comes from the host either way — the image only provides the userland.

**Can I migrate kopsrox VMs to other hosts in my Proxmox cluster?**

This is mostly supported but largely untested — kopsrox builds everything on the single configured `proxmox_node`. Note that microvms do not support live migration.

**The guest agent times out, or nodes can't reach the internet.**

Check the `network_*` settings in `kopsrox.ini` — k3s ships baked into the image, but nodes still need internet access to reach the S3 endpoint for etcd snapshots. `./kopsrox.py node utility` followed by `node terminal` is a good way to investigate, with root autologin on the serial console.

**IPv6?**

Disabled via the kernel command line (`ipv6.disable=1`). k3s and Flannel are configured for IPv4 only.

**How do I re-add the m1 master?**

Run `./kopsrox.py node k3s-uninstall <cluster>-m1`, then `node rejoin-slave <cluster>-m1`. If m1 held the VIP, kube-vip moves it automatically. This requires at least one other healthy master (masters = 3) — with a single master there is no other node to rejoin against, and `cluster restore` is the right tool instead.

## Acknowledgements

kopsrox is built on [pve-microvm](https://github.com/rcarmo/pve-microvm), [k3s](https://k3s.io), and [kube-vip](https://kube-vip.io/).
