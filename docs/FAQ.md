# kopsrox FAQ

:question: __why microvms instead of full VMs?__

microvms ( via [pve-microvm](https://github.com/rcarmo/pve-microvm) ) skip BIOS/UEFI, PCI enumeration and most emulated hardware - a node boots in about 1 second and a whole cluster builds in ~2.5 minutes. same KVM isolation as a full VM, tiny fraction of the overhead

:question: __why does kopsrox need its own kernel?__

microvm guests boot a kernel supplied by the host and have no `/lib/modules`, so everything k3s needs ( vxlan for flannel, veth, netfilter, ipset, bpf for cgroup v2 ... ) must be compiled in. the stock pve-microvm kernel doesn't include these - `dev/build-kopsrox-kernel.sh` builds one that does ( one time, ~10 minutes )

:question: __what happened to the proxmox CSI driver and cloud controller?__

they don't work on microvm: the guest has no DMI/SMBIOS so the cloud controller's identity check can never pass, and the CSI driver needs disk hotplug into running guests which pve-microvm doesn't support yet. kopsrox sets the provider-id and uses the k3s local-path provisioner for storage instead. NFS also works fine ( `extra_packages = nfs-common` is the default and the kernel has the NFS client built in )

:question: __qm shutdown / qm reboot don't work from the proxmox UI?__

they do since pve-microvm v0.3.19 ( kopsrox contributed the fix ) - and remember to `systemctl restart pvedaemon` after any pve-microvm install or upgrade, or VMs started via the API keep using the old code

:question: __careful with qm stop!__

`qm stop` on a microvm is a hard power cut ( no ACPI power button ) - anything the guest hasn't synced to disk is lost. use `./kopsrox.py node reboot` or `qm shutdown` for graceful operations

:question: __can I use a different OCI image than ubuntu?__

set `oci_image` in `kopsrox.ini` - other apt based images ( eg `debian:trixie-slim` ) should work but are untested with kopsrox. the kernel comes from the host either way, the image only provides the userland

:question: __can I migrate kopsrox vms to other hosts in my proxmox cluster?__

mostly supported but largely untested - kopsrox builds everything on the one configured `proxmox_node`. note microvms don't support live migration

:question: __the guest agent times out / nodes can't reach the internet?__

check the `network_*` settings in `kopsrox.ini` - the nodes need internet access to download k3s. `./kopsrox.py node utility` then `node terminal` to poke around is a good way to debug ( root autologin on the serial console )

:question: __ipv6?__

disabled via the kernel command line ( `ipv6.disable=1` ) - k3s and flannel are configured ipv4-only

:question: __how do I re-add the m1 master?__

`./kopsrox.py node k3s-uninstall <cluster>-m1` then `node rejoin-slave <cluster>-m1` - if m1 held the VIP, kube-vip moves it automatically
