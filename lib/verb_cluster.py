#!/usr/bin/env python3

import os

from kopsrox_config import cluster_id, cluster_name, get_k3s_token, list_kopsrox_vm, masterid, masters, network_ip, vmnames, vms, workers
from kopsrox_k3s import cluster_info, cluster_plan_total, k3s_init_node, k3s_rm_cluster, k3s_update_cluster, kubectl
from kopsrox_kmsg import kabort, kmsg, kplan
from kopsrox_proxmox import clone


# restore from an etcd snapshot ( latest when snapshot is None )
def cluster_restore(snapshot: str | None = None) -> None:
    kname = 'cluster_restore'

    # a snapshot's bootstrap data is encrypted with the cluster token, so restore
    # is impossible without the saved one - check before k3s_rm_cluster destroys
    # everything, not after ( a missing snapshot name is validated later, on the
    # rebuilt master, since a restore discards current state anyway )
    if get_k3s_token() is None:
        kabort(kname, f'{cluster_name}.k3stoken not found - it is required to decrypt the snapshot')

    # removals + m1 clone/restore-init/export + m1 recheck + rebuilt slaves and workers
    removals = len([v for v in list_kopsrox_vm() if vmnames[v] not in [f'{cluster_name}-i0', f'{cluster_name}-u1']])
    kplan(removals + 4 + 2 * (masters - 1) + 2 * workers, f'{cluster_name} cluster restore')

    k3s_rm_cluster()
    kmsg(kname, f'id:{cluster_id} name:{cluster_name}', 'sys')
    clone(masterid)
    k3s_init_node(masterid, 'restore', snapshot)

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


# run a command on every node ( skips the image template ) via the guest agent
def cluster_exec(arg: str | None) -> None:
    for vmid in vms:
        if vmid != cluster_id:
            kmsg('cluster_exec', f'{vmnames[vmid]} {arg}')
            os.system(f'sudo qm guest exec {vmid} {arg}')
    exit(0)


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

    # restore from an etcd snapshot - optional snapshot name, latest when omitted
    if cmd == 'restore':
        cluster_restore(arg)

    # run a command on every node
    if cmd == 'exec':
        cluster_exec(arg)

    # create new cluster / master server
    if cmd == 'create':
        cluster_create()

    # destroy the cluster
    if cmd == 'destroy':
        cluster_destroy()
