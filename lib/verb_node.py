#!/usr/bin/env python3

import os

from kopsrox_config import cluster_id, localpass, localuser, network_ip, vmip, vmnames, vms
from kopsrox_k3s import cluster_info, k3s_forget_node, k3s_init_node, k3s_remove_node
from kopsrox_kmsg import kabort, kmsg, kstep
from kopsrox_proxmox import clone, node_reboot_wait, qa_exec


def node_terminal(vmid: int) -> None:
    kmsg('node_terminal', f'root autologin on console - or u/p: {localuser} / {localpass}', 'sys')
    os.system(f'sudo qm terminal {vmid}')
    exit(0)


def node_ssh(vmid: int) -> None:
    os.system(f'ssh -l {localuser} {vmip(vmid)} -o StrictHostKeyChecking=no ')
    exit(0)


def node_destroy(vmid: int) -> None:
    k3s_remove_node(vmid)
    exit(0)


# in-guest reboot via the agent - qm reboot hangs on microvm
def node_reboot(vmid: int) -> None:
    node_reboot_wait(vmid)
    exit(0)


# never use k3s-uninstall.sh: both units are baked into every image, so its
# "other role installed" guard always trips - it skips the shared binary/data
# yet still deletes its own unit file. use k3s-killall.sh and wipe by hand
def node_k3s_uninstall(vmid: int) -> None:
    vmname = vmnames[vmid]
    with kstep('node_k3s-uninstall', vmname):
        qa_exec(vmid, '/usr/local/bin/k3s-killall.sh > /k3s_uninstall.log 2>&1')
        qa_exec(vmid, 'rm -rf /var/lib/rancher /etc/rancher/k3s && mkdir -p /etc/rancher/k3s')
        qa_exec(vmid, f'ip addr del {network_ip}/32 dev eth0 2>/dev/null; true')
        if not k3s_forget_node(vmid):
            kmsg('node_k3s-uninstall', f'no other master up - {vmname} left registered in the cluster', 'sys')
    exit(0)


# works even after node_k3s_uninstall wiped everything - the unit is baked in
def node_rejoin_slave(vmid: int) -> None:
    k3s_init_node(vmid, 'slave')
    exit(0)


def node_utility() -> None:
    kname = 'node_utility'

    utility_vm_id = cluster_id + 4

    if utility_vm_id not in vms:
        kmsg(kname, 'creating utility node', 'sys')
        clone(utility_vm_id)
    else:
        kmsg(kname, 'utility node already exists')
    cluster_info()


def run(cmd: str, arg: str | None = None) -> None:

    kname = 'node_' + cmd

    if cmd not in ['utility']:

        for vmid in vms:

            if arg == vmnames[vmid]:
                kmsg(kname, arg)

                if cmd == 'terminal':
                    node_terminal(vmid)

                if cmd == 'ssh':
                    node_ssh(vmid)

                if cmd == 'destroy':
                    node_destroy(vmid)

                if cmd == 'reboot':
                    node_reboot(vmid)

                if cmd == 'k3s-uninstall':
                    node_k3s_uninstall(vmid)

                if cmd == 'rejoin-slave':
                    node_rejoin_slave(vmid)

        kabort(kname, f'{arg} vm not found')

    if cmd == 'utility':
        node_utility()
