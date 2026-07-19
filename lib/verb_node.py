#!/usr/bin/env python3

import os

from kopsrox_config import cluster_id, localpass, localuser, vmip, vmnames, vms
from kopsrox_k3s import cluster_info, k3s_init_node, k3s_remove_node
from kopsrox_kmsg import kabort, kmsg
from kopsrox_proxmox import clone, node_reboot_wait


# cmd runs through all vms
def node_cluster_exec(arg: str | None) -> None:
    for vmid in vms:
        if vmid != cluster_id:
            kmsg('node_cluster-exec', f'{vmnames[vmid]} {arg}')
            os.system(f'sudo qm guest exec {vmid} {arg}')
    exit(0)


# terminal
def node_terminal(vmid: int) -> None:
    kmsg('node_terminal', f'root autologin on console - or u/p: {localuser} / {localpass}', 'sys')
    os.system(f'sudo qm terminal {vmid}')
    exit(0)


# ssh command
def node_ssh(vmid: int) -> None:
    os.system(f'ssh -l {localuser} {vmip(vmid)} -o StrictHostKeyChecking=no ')
    exit(0)


# destroy vm
def node_destroy(vmid: int) -> None:
    k3s_remove_node(vmid)
    exit(0)


# reboot in-guest via the agent - qm reboot hangs on microvm as
# pve-microvm omits the qmeventd socket that reaps the halted process
def node_reboot(vmid: int) -> None:
    node_reboot_wait(vmid)
    exit(0)


# k3s uninstall
def node_k3s_uninstall(vmid: int) -> None:
    os.system(f'sudo qm guest exec {vmid} /usr/local/bin/k3s-uninstall.sh')
    exit(0)


# rejoin slave
def node_rejoin_slave(vmid: int) -> None:
    k3s_init_node(vmid, 'slave')
    exit(0)


# create utility node
def node_utility() -> None:
    kname = 'node_utility'

    # define id of utility server
    utility_vm_id = cluster_id + 4

    # check to see if already exists
    if utility_vm_id not in vms:
        kmsg(kname, 'creating utility node', 'sys')
        clone(utility_vm_id)
    else:
        kmsg(kname, 'utility node already exists')
    cluster_info()


def run(cmd: str, arg: str | None = None) -> None:

    # define kname
    kname = 'node_' + cmd

    # cmd runs through all vms
    if cmd == 'cluster-exec':
        node_cluster_exec(arg)

    # all commands aside from utility require a hostname passed - so check them here
    if cmd not in ['utility']:

        # for each vmid in list of vms generated in kopsrox_config
        for vmid in vms:

            # if passed arg matches vmname
            if arg == vmnames[vmid]:
                kmsg(kname, arg)

                # terminal
                if cmd == 'terminal':
                    node_terminal(vmid)

                # ssh command
                if cmd == 'ssh':
                    node_ssh(vmid)

                # destroy vm
                if cmd == 'destroy':
                    node_destroy(vmid)

                # reboot
                if cmd == 'reboot':
                    node_reboot(vmid)

                # k3s uninstall
                if cmd == 'k3s-uninstall':
                    node_k3s_uninstall(vmid)

                # rejoin slave
                if cmd == 'rejoin-slave':
                    node_rejoin_slave(vmid)

        # vm not found
        kabort(kname, f'{arg} vm not found')

    # create utility node
    if cmd == 'utility':
        node_utility()
