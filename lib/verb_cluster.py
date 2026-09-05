#!/usr/bin/env python3

import os

from kopsrox_config import cluster_id, cluster_name, get_k3s_token, list_kopsrox_vm, masterid, masters, network_ip, vmnames, vms, workers
from kopsrox_k3s import cluster_info, cluster_plan_total, k3s_init_node, k3s_rm_cluster, k3s_update_cluster, kubectl
from kopsrox_kmsg import kabort, kmsg, kplan
from kopsrox_proxmox import clone


def cluster_restore(snapshot: str | None = None) -> None:
    kname = 'cluster_restore'

    # a snapshot is encrypted with the cluster token, so check for the saved one
    # BEFORE k3s_rm_cluster destroys everything, not after
    if get_k3s_token() is None:
        kabort(kname, f'{cluster_name}.k3stoken not found - it is required to decrypt the snapshot')

    removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
    kplan(removals + 4 + 2 * (masters - 1) + 2 * workers, f'{cluster_name} cluster restore')

    k3s_rm_cluster()
    kmsg(kname, f'id:{cluster_id} name:{cluster_name}', 'sys')
    clone(masterid)
    k3s_init_node(masterid, 'restore', snapshot)

    # nothing garbage collects stale node objects/secrets without a cloud
    # controller, and they make rebuilt nodes get rejected
    restored_master = vmnames[masterid]
    nodes_out = kubectl('get nodes -o name')  # 'node/anchovy-m1\nnode/anchovy-w1'
    stale = [n.split('/', 1)[-1] for n in nodes_out.split('\n') if n.startswith('node/')]
    for vmname in stale:
        if vmname != restored_master:
            kubectl(f'delete node {vmname} --ignore-not-found')
            kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found')

    cluster_info()
    kmsg(kname, f'restore completed')
    k3s_update_cluster()


def cluster_create() -> None:
    kname = 'cluster_create'

    kplan(cluster_plan_total() + 1, f'{cluster_name} cluster create')

    if not masterid in list_kopsrox_vm():
        kmsg(kname, f'{cluster_name} id {cluster_id} network {network_ip} m {masters} w {workers}', 'sys')
        clone(masterid)

    k3s_init_node()

    k3s_update_cluster()


# run a command on every node, skipping the image template
def cluster_exec(arg: str | None) -> None:
    for vmid in vms:
        if vmid != cluster_id:
            kmsg('cluster_exec', f'{vmnames[vmid]} {arg}')
            os.system(f'sudo qm guest exec {vmid} {arg}')
    exit(0)


def cluster_destroy() -> None:
    kname = 'cluster_destroy'
    removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
    kplan(removals, f'{cluster_name} cluster destroy')
    kmsg(kname, f'{cluster_name}', 'err')
    k3s_rm_cluster()


def run(cmd: str, arg: str | None = None) -> None:

    if cmd == 'info':
        cluster_info()

    if cmd == 'update':
        kplan(cluster_plan_total(), f'{cluster_name} cluster update')
        k3s_update_cluster()

    if cmd == 'restore':
        cluster_restore(arg)

    if cmd == 'exec':
        cluster_exec(arg)

    if cmd == 'create':
        cluster_create()

    if cmd == 'destroy':
        cluster_destroy()
