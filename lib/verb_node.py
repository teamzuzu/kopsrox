#!/usr/bin/env python3

import os

from kopsrox_artifacts import k3s_server_config
from kopsrox_config import cluster_id, localpass, localuser, network_ip, vmip, vmnames, vms
from kopsrox_k3s import cluster_info, k3s_forget_node, k3s_init_node, k3s_remove_node
from kopsrox_kmsg import kabort, kmsg, kstep
from kopsrox_proxmox import clone, node_reboot_wait, qa_exec, qa_write


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


# k3s uninstall - also drops any VIP address k3s-uninstall left orphaned on
# this node ( it kills kube-vip's container before it can release the VIP )
# and forgets the node's cluster registration, so a later rejoin under the
# same name is accepted instead of hitting a stale node object / duplicate
# etcd member
def node_k3s_uninstall(vmid: int) -> None:
    vmname = vmnames[vmid]
    with kstep('node_k3s-uninstall', vmname):
        qa_exec(vmid, '/usr/local/bin/k3s-uninstall.sh > /k3s_uninstall.log 2>&1')
        qa_exec(vmid, f'ip addr del {network_ip}/32 dev eth0 2>/dev/null; true')
        if not k3s_forget_node(vmid):
            kmsg('node_k3s-uninstall', f'no other master up - {vmname} left registered in the cluster', 'sys')
    exit(0)


# rejoin slave
def node_rejoin_slave(vmid: int) -> None:
    # k3s-uninstall wipes /etc/rancher/k3s ( directory and all ) - node_prepare
    # only creates it once at clone time, so recreate it before reinstalling
    qa_exec(vmid, 'mkdir -p /etc/rancher/k3s')
    qa_write(vmid, '/etc/rancher/k3s/config.yaml', k3s_server_config())
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
