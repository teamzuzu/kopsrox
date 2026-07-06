#  :hammer_and_wrench: - Setup 

##  requirements

- Proxmox VE with root access / a user who can 'sudo su' without a password
- network with internet access configured in proxmox as a bridge or a proxmox sdn network
- a range of 10 free Proxmox qm/virtual machine id 'vmids' eg 600 to 610
- a range of 10 IP's on a network with internet access for kopsrox to work with eg 192.168.0.160 to 192.168.0.170

## install 

- get one of the releases or stable branches - the 'main' branch can often be a bit broken
- sudo apt install python3-termcolor python3-proxmoxer python3-requests -y
- install pve-microvm on the proxmox node - see https://github.com/rcarmo/pve-microvm
- build the kopsrox microvm kernel ( one time - the stock pve-microvm kernel can't run k3s ):

`./dev/build-kopsrox-kernel.sh`

## generate api key ( to use below ) 

`sudo pvesh create /access/users/root@pam/token/kopsrox`

`sudo pveum acl modify / --roles Administrator --user root@pam  --token 'root@pam!kopsrox'`

## create kopsrox.ini

run `./kopsrox.py` and an example _kopsrox.ini_ will be generated - you will need to edit this for your setup

Most values should be hopefully easy to work out hopefully. 

kopsrox uses a simple static id/ip assignment scheme based on the `[cluster] - cluster_id` and `[kopsrox] - network_ip` settings 

For example:

```
[kopsrox]
network_ip = 192.168.0.170

[cluster]
cluster_id = 620
cluster_name = kopsrox
```

would result in this cluster layout:

|-|vmid|ip|type|host|
|--|--|--|--|--|
|0|620|192.168.0.170|image/VIP|kopsrox-i1|
|1|621|192.168.0.171|master 1|kopsrox-m1|
|2|622|192.168.0.172|master 2|kopsrox-m2|
|3|623|192.168.0.173|master 3|kopsrox-m3|
|4|624|192.168.0.174|utility 1|kopsrox-u1|
|5|625|192.168.0.175|worker 1|kopsrox-w1|
|6|626|192.168.0.176|worker 2|kopsrox-w2|
|7|627|192.168.0.177|worker 3|kopsrox-w3|
|8|628|192.168.0.178|worker 4|kopsrox-w4|
|9|629|192.168.0.179|worker 5|kopsrox-w5|

The VIP IP ( here 192.168.0.170 ) is used by kube-vip to provide a highly available IP for the API when you have 3 master nodes
