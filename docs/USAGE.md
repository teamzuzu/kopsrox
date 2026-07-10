# :hamburger: kopsrox usage

`./kopsrox.py [verb] [command] [arg]`

- [image](#image)
- [cluster](#cluster)
- [k3s](#k3s)
- [etcd](#etcd)
- [node](#node)

## :cyclone: image <a name=image>

### create / update
- builds a cluster-generic microvm template from the OCI image set in `kopsrox.ini` ( via a patched copy of [pve-microvm-template](https://github.com/rcarmo/pve-microvm) - log in `kopsrox-image.log` )
- verifies the rootfs, sets the kopsrox kernel and converts to a template on vmid `cluster_id`
- nothing cluster-specific is baked in - node identity and k3s config are pushed per node at create time via the guest agent

### info
- prints the template description ( source image, k3s version, creation time ) and storage volume

### :warning: destroy
- deletes the image template

## :cyclone: cluster <a name=cluster>

### create
- creates a fresh cluster - clones the template into master ( and worker ) nodes per `kopsrox.ini`, installs k3s and exports the kubeconfig + token
- safe to re-run - if a working master exists it acts like `cluster update`

### update
- reconciles the running cluster against `masters` / `workers` in `kopsrox.ini` - adds or drains+removes nodes as needed

### info
- lists vmids, hostnames, ips and which proxmox host they run on, plus `kubectl get nodes`
- shows which node currently holds the VIP

### restore
- rebuilds the whole cluster from the **latest** S3 etcd snapshot - works even if all nodes are gone
- restores the master then reconciles the rest per `kopsrox.ini`

### :warning: destroy
- destroys the cluster ( NO WARNING! ) - workers drained and removed first, then masters
- the image template and utility node are left alone

## :cyclone: k3s <a name=k3s>

### export-token
- exports the cluster k3s token to `<cluster_name>.k3stoken` ( needed for restores - keep it safe )

### kubeconfig
- exports the kubeconfig to `<cluster_name>.kubeconfig` - patched to point at the VIP instead of 127.0.0.1

### check-config
- runs `k3s check-config` on the master and shows the output ( the kopsrox kernel exposes `/proc/config.gz` so this works fully )

### kubectl [cmd]
- quick way to run kubectl on the master via the guest agent:

`./kopsrox.py k3s kubectl get events -A`

### reload-kubevip
- restarts the kube-vip daemonset

## :cyclone: etcd <a name=etcd>

### snapshot
- takes an etcd snapshot and uploads it to the configured S3 storage

### list
- lists this cluster's snapshots in S3

### restore [snapshot]
- restores the cluster from a specific snapshot ( get names from `etcd list` )
- for "just restore the latest" use `cluster restore`

### prune
- deletes old snapshots per the retention policy

## :cyclone: node <a name=node>

### utility
- creates a spare "utility" node ( `-u1` ) - fully configured but no k3s - handy for testing and debugging

### terminal [hostname]
- connects to the node's serial console via `qm terminal` - root autologin, no password needed ( ctrl-o to exit )

### ssh [hostname]
- ssh to a node as the configured user ( requires your ssh key in `kopsrox.ini` )

### reboot [hostname]
- reboots the node via the guest agent and waits for it to come back ( microvms reboot in about a second :zap: )

### :warning: destroy [hostname]
- drains, removes and deletes the node ( NO WARNING )

### k3s-uninstall [hostname]
- uninstalls k3s from the node - for experimenting with reinstalls

### rejoin-slave [hostname]
- reinstalls k3s on a master and rejoins it to the cluster

### cluster-exec [command]
- runs a command on every node via the guest agent
