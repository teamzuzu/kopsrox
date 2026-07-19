#!/usr/bin/env python3

from kopsrox_config import cluster_id, cluster_name, list_kopsrox_vm, masterid, masters, network_ip, vmnames, workers
from kopsrox_k3s import cluster_info, cluster_plan_total, k3s_init_node, k3s_rm_cluster, k3s_update_cluster, kubectl
from kopsrox_kmsg import kmsg, kplan
from kopsrox_proxmox import clone


# restore from latest etcd snapshot
def cluster_restore() -> None:
    kname = 'cluster_restore'

    # removals + m1 clone/restore-init/export + m1 recheck + rebuilt slaves and workers
    removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
    kplan(removals + 4 + 2 * (masters - 1) + 2 * workers, f'{cluster_name} cluster restore')

    k3s_rm_cluster()
    kmsg(kname, f'id:{cluster_id} name:{cluster_name}', 'sys')
    clone(masterid)
    k3s_init_node(masterid, 'restore')

    # the restored datastore contains stale node objects and node password
    # secrets for nodes that no longer exist - without a cloud controller
    # nothing garbage collects them and rebuilt nodes get rejected
    for vmid in vmnames:
        vmname = vmnames[vmid]
        if vmname not in [f'{cluster_name}-i0', f'{cluster_name}-m1', f'{cluster_name}-u1']:
            kubectl(f'delete node {vmname} --ignore-not-found')
            kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found')

    cluster_info()
    kmsg(kname, f'restore completed')
    k3s_update_cluster()


# create new cluster / master server
def cluster_create() -> None:
    kname = 'cluster_create'

    # + 1 - the master init runs here and again as a recheck inside k3s_update_cluster
    kplan(cluster_plan_total() + 1, f'{cluster_name} cluster create')

    # if masterid not found running
    if not masterid in list_kopsrox_vm():
        kmsg(kname, f'{cluster_name} id {cluster_id} network {network_ip} m {masters} w {workers}', 'sys')
        clone(masterid)

    # install k3s on master
    k3s_init_node()

    # perform rest of cluster creation
    k3s_update_cluster()


# destroy the cluster
def cluster_destroy() -> None:
    kname = 'cluster_destroy'
    removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
    kplan(removals, f'{cluster_name} cluster destroy')
    kmsg(kname, f'{cluster_name}', 'err')
    k3s_rm_cluster()


def run(cmd: str, arg: str | None = None) -> None:

    # info
    if cmd == 'info':
        cluster_info()

    # update cluster
    if cmd == 'update':
        kplan(cluster_plan_total(), f'{cluster_name} cluster update')
        k3s_update_cluster()

    # restore from latest etcd snapshot
    if cmd == 'restore':
        cluster_restore()

    # create new cluster / master server
    if cmd == 'create':
        cluster_create()

    # destroy the cluster
    if cmd == 'destroy':
        cluster_destroy()
