# :hammer_and_wrench: setup

## requirements

- Proxmox VE with root access / a user who can `sudo` without a password ( kopsrox runs directly on the proxmox node )
- [pve-microvm](https://github.com/rcarmo/pve-microvm) **v0.3.19 or later** installed on the node
- a network with internet access configured in proxmox as a bridge or a proxmox sdn network
- a range of 10 free vmids eg 600 to 610
- a range of 10 free IPs on that network eg 192.168.0.160 to 192.168.0.170

## install

python dependencies:

```
sudo apt install python3-termcolor python3-proxmoxer python3-requests -y
```

install pve-microvm - see the [pve-microvm install docs](https://github.com/rcarmo/pve-microvm/blob/main/docs/installation.md):

```
curl -sLO $(curl -s https://api.github.com/repos/rcarmo/pve-microvm/releases/latest | grep browser_download_url | grep '.deb' | cut -d'"' -f4)
sudo dpkg -i pve-microvm_*.deb
sudo systemctl restart pvedaemon
```

> :warning: always restart `pvedaemon` after installing or upgrading pve-microvm - it keeps the old code loaded in memory and VMs started via the API silently miss the new version until you do

build the kopsrox microvm kernel ( one time - the stock pve-microvm kernel is missing features k3s needs like vxlan and veth ):

```
./dev/build-kopsrox-kernel.sh
```

this fetches [pve-microvm's kernel builder](https://github.com/rcarmo/pve-microvm/tree/main/kernel), merges in the k3s config fragment from `lib/scripts/kopsrox-kernel.config` and installs `/usr/share/pve-microvm/vmlinuz-kopsrox` + `initrd-kopsrox`

## generate an api key

```
sudo pvesh create /access/users/root@pam/token/kopsrox
sudo pveum acl modify / --roles Administrator --user root@pam --token 'root@pam!kopsrox'
```

put the token value in `kopsrox.ini` ( see below )

## create kopsrox.ini

run `./kopsrox.py` and an example `kopsrox.ini` will be generated - edit it for your setup

most values should hopefully be easy to work out :crossed_fingers: a few worth knowing about:

- `oci_image` - the OCI image nodes are built from ( default `ubuntu:24.04` - other apt based images should work but are untested )
- `localuser` / `localpass` / `localsshkey` - the user created in every node ( via the guest agent - there's no cloud-init on microvm )
- `network_mtu` - applied inside each node - set to 1450 if using a proxmox sdn
- `s3_*` - S3 credentials for etcd snapshots ( works great with cloudflare r2 / backblaze b2 / minio )

## ids and ips

kopsrox uses a simple static id/ip scheme based on `cluster_id` and `network_ip`. for example:

```
network_ip = 192.168.0.170
cluster_id = 620
cluster_name = kopsrox
```

gives this layout:

|-|vmid|ip|type|host|
|--|--|--|--|--|
|0|620|192.168.0.170|image/VIP|kopsrox-i0|
|1|621|192.168.0.171|master 1|kopsrox-m1|
|2|622|192.168.0.172|master 2|kopsrox-m2|
|3|623|192.168.0.173|master 3|kopsrox-m3|
|4|624|192.168.0.174|utility|kopsrox-u1|
|5|625|192.168.0.175|worker 1|kopsrox-w1|
|6|626|192.168.0.176|worker 2|kopsrox-w2|
|7|627|192.168.0.177|worker 3|kopsrox-w3|
|8|628|192.168.0.178|worker 4|kopsrox-w4|
|9|629|192.168.0.179|worker 5|kopsrox-w5|

`network_ip` itself ( here 192.168.0.170 ) is the kube-vip VIP - a highly available IP for the kube api and traefik when you run 3 masters
