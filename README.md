# kopsrox

kopsrox creates and manages simple HA [k3s](https://k3s.io) clusters on [Proxmox VE](https://www.proxmox.com/) using lightweight microvms from [pve-microvm](https://github.com/rcarmo/pve-microvm) :rocket:

- nodes are microvms built from plain OCI images ( default `ubuntu:24.04` ) - no ISOs or cloud images to mess around with
- microvms boot in about **1 second** - a fresh HA-capable cluster is up in around 2.5 minutes :zap:
- add or remove master/worker nodes by editing a simple config file and running one command :pray:
- [kube-vip](https://kube-vip.io/) built in - a highly available VIP for the kube api and traefik :atom:
- storage via the k3s local-path provisioner
- easy etcd S3 snapshot/restore - rebuild your whole cluster from S3 with one command :floppy_disk:
- everything happens through the qemu guest agent - no cloud-init, no ssh required, and it works before the node even has networking :nerd_face:
- exports your kubeconfig and k3s token automatically

get it: https://github.com/simonccc/kopsrox/releases

# docs

- [SETUP.md](docs/SETUP.md) - requirements and installation
- [GETSTARTED.md](docs/GETSTARTED.md) - your first cluster in 5 commands
- [USAGE.md](docs/USAGE.md) - every command explained
- [FAQ.md](docs/FAQ.md) - common questions and microvm gotchas

# thanks

kopsrox stands on the shoulders of [pve-microvm](https://github.com/rcarmo/pve-microvm), [k3s](https://k3s.io) and [kube-vip](https://kube-vip.io/) :heart:
