# kopsrox

- NEWS: kopsrox now uses the new micro vms supported in pve https://github.com/rcarmo/pve-microvm

- kopsrox is a script to help create and manage simple ha k3s clusters on ProxmoxVE
- nodes are lightweight microvms built from upstream OCI images - no iso's or cloud images to mess around with
- add more master/worker k3s nodes using a simple config file and cli interface :pray:
- kube-vip ( https://kube-vip.io/ ) built in providing full HA setup for the kube api and traefik :atom:
- storage via the k3s local-path provisioner ( the proxmox csi driver needs disk hotplug which microvms don't support yet )
- easy management of etcd S3 snapshot/restore operations - easily restore a cluster from s3! :floppy_disk:
- export the k3s token, your kubeconfig etc etc - its all automatic  :nerd_face:

  get it https://github.com/simonccc/kopsrox/releases

# docs
 - [SETUP.md](docs/SETUP.md)
 - [GETSTARTED.md](docs/GETSTARTED.md)
 - [USAGE.md](docs/USAGE.md)
 - [FAQ.md](docs/FAQ.md)

# in progress
 - Recent: add proxmox-cloud-controller-manager
 - Going to check proxmox CSI driver
