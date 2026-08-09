#!/usr/bin/env python3

# imports
import os
import re
import time

import requests

from kopsrox_artifacts import k3s_config, kopsrox_manifest
from kopsrox_config import (
    cluster_id,
    cluster_name,
    conf_check_master_up,
    get_k3s_token,
    k3s_version,
    list_kopsrox_vm,
    masterid,
    masters,
    network_ip,
    network_mask,
    vmip,
    vmnames,
    workers,
)
from kopsrox_proxmox import clone, internet_check, prox_destroy, qa_exec, qa_write
from kopsrox_kmsg import kabort, kmsg, kplan_tick, kstep


# check for k3s status
def k3s_check(vmid: int) -> bool:

    # quiet probe - kubectl may not be installed yet so never fail the exec ( || true )
    get_node = qa_exec(masterid, f'/usr/local/bin/kubectl get node {vmnames[vmid]} 2>&1 || true')

    # return true if Ready
    # word boundary as NotReady also contains Ready
    return bool(re.search(r'\bReady\b', get_node))

# systemd unit baked into the image for this role at image-build time
# ( see verb_image.py ) - server role covers master/slave/restore, agent role
# is the separate k3s-agent unit k3s's installer creates for workers
def k3s_service(nodetype: str) -> str:
    return 'k3s-agent' if nodetype == 'worker' else 'k3s'

# write this node's config.yaml and start the systemd unit already baked
# into the image - no install, no internet dependency at join time, every
# role-specific flag lives in config.yaml. the /etc/rancher/k3s dir and the
# kube-vip/traefik manifest are also baked into the image ( verb_image.py ) so
# a fresh clone needs no mkdir or manifest write here - only the restore path
# rewrites the manifest, since restoring wipes /var/lib/rancher.
# token defaults to the saved token file ( reused so recreating a cluster of
# the same name is deterministic ) - pass token='' to force a fresh identity
def k3s_join(vmid: int, nodetype: str, token: str | None = None) -> None:
    if token is None:
        token = get_k3s_token() or ''
    service = k3s_service(nodetype)
    qa_write(vmid, '/etc/rancher/k3s/config.yaml', k3s_config(nodetype, token))
    qa_exec(vmid, f'systemctl enable --now {service} > /k3s_{nodetype}_install.log 2>&1')

# wait for a node to report Ready - each k3s_check is a kubectl run so takes a second or two
def k3s_wait_ready(vmid: int, vmname: str) -> None:
    wait: int = 20
    for count in range(wait):
        if k3s_check(vmid):
            return
        time.sleep(1)
    kabort('k3s_check', f'timed out after {wait}s for {vmname}')

# create a master/slave/worker
def k3s_init_node(vmid: int = masterid, nodetype: str = 'master', snapshot: str | None = None) -> None:

    # nodetype error check
    if nodetype not in ['master', 'slave', 'worker', 'restore']:
        kabort('k3s_init-node', f'{nodetype} invalid nodetype')

    # check node has internet - aborts itself on failure
    internet_check(vmid)

    # map vmname
    vmname = vmnames[vmid]

    # k3s already up on this node
    if k3s_check(vmid):
        kmsg(f'k3s_{nodetype}', f'{vmname} Ready')
        kplan_tick()
        return

    # master / slave / worker
    if nodetype in ['master', 'worker', 'slave']:
        with kstep(f'k3s_{nodetype}', f'starting k3s {k3s_version} on {vmname}') as step:
            k3s_join(vmid, nodetype)
            step.msg = f'waiting for {vmname} Ready'
            k3s_wait_ready(vmid, vmname)
        kplan_tick()

    # restore - bootstrap a throwaway single-node master, list the S3 snapshots
    # from it ( etcd-snapshot ls needs a running api server to fetch its CA certs ),
    # then reset+restore etcd from the chosen snapshot. the bootstrap token is ''
    # ( a throwaway identity ) since the rm -rf below wipes it before the real
    # restore anyway. the restore then passes the SAVED token to --cluster-reset -
    # k3s encrypts a snapshot's bootstrap data with the cluster token's password
    # and can only decrypt it with that same token ( the cluster was created with
    # the saved token, so its snapshots carry it ). without --token the reset
    # fatals 'token does not exist' - which, unchecked, once booted a fresh empty
    # cluster in the restore's place. snapshot=None restores the latest, else named
    if nodetype == 'restore':
        token = get_k3s_token()
        if not token:
            kabort('k3s_restore', f'{cluster_name}.k3stoken is required to restore - it must be '
                   f'the token the target snapshot was taken with, or the bootstrap data cannot be decrypted')

        with kstep('k3s_restore', f'bootstrapping {vmname} to read snapshots') as step:
            k3s_join(vmid, 'master', token='')
            step.msg = f'waiting for {vmname} Ready'
            k3s_wait_ready(vmid, vmname)

            # select / validate the snapshot from the s3 repo. the destroy already
            # happened, but a restore discards current state by definition, so a
            # bad name just means re-running with the right one
            step.msg = 'listing snapshots'
            ls_out = qa_exec(vmid, '/usr/local/bin/k3s etcd-snapshot ls 2>&1')
            available = sorted(line.split()[0] for line in ls_out.split('\n')
                               if line.split() and re.search(f'kopsrox-{cluster_name}', line.split()[0]))
            if not available:
                kabort('k3s_restore', f'no snapshots found for {cluster_name} in the s3 repo')
            if snapshot is None:
                snapshot = available[-1]
            elif snapshot not in available:
                kabort('k3s_restore', f'snapshot "{snapshot}" not found - available:\n' + '\n'.join(available))

        # name the snapshot up front - the reset below is destructive and slow
        kmsg('k3s_restore', f'restoring {vmname} from snapshot {snapshot}', 'sys')

        with kstep('k3s_restore', f'resetting etcd from {snapshot}') as step:
            qa_exec(vmid, 'systemctl stop k3s && rm -rf /var/lib/rancher/')

            # --token lets k3s decrypt the snapshot's bootstrap data. the k3s
            # output is redirected, so read the exit code back and confirm the
            # reset actually succeeded - otherwise a wrong token / missing snapshot
            # fatals here and, unchecked, an empty cluster-init cluster boots in
            # its place ( exactly the 'restored cluster is new' failure this fixes )
            rc = qa_exec(vmid, f'/usr/local/bin/k3s server --cluster-reset '
                               f'--cluster-reset-restore-path={snapshot} --token={token} '
                               f'> /k3s_restore.log 2>&1; echo RC=$?').rsplit('RC=', 1)[-1].strip()
            restore_log = qa_exec(vmid, 'cat /k3s_restore.log')
            if rc != '0' or re.search('level=fatal', restore_log):
                kabort('k3s_restore', f'snapshot restore failed ( rc={rc} ) - /k3s_restore.log on {vmname}:\n{restore_log}')

            # the reset wiped /var/lib/rancher, taking the baked-in manifests dir
            # with it - reapply kube-vip/traefik or the vip never comes back
            qa_exec(vmid, 'mkdir -p /var/lib/rancher/k3s/server/manifests')
            qa_write(vmid, f'/var/lib/rancher/k3s/server/manifests/kopsrox-{cluster_name}.yaml', kopsrox_manifest())
            qa_exec(vmid, 'systemctl start k3s')
            step.msg = f'waiting for {vmname} Ready'
            k3s_wait_ready(vmid, vmname)
        kplan_tick()

    # final steps for first master / restore export kubeconfig and token
    if nodetype in ['master', 'restore']:
        with kstep('k3s_export', 'kubeconfig + token'):
            kubeconfig()
            export_k3s_token(restore=(nodetype == 'restore'))
        kplan_tick()

# remove a node
def k3s_remove_node(vmid: int) -> None:

    # get vmname
    vmname = vmnames[vmid]

    with kstep('k3s_remove-node', vmname):
        if vmname != f'{cluster_name}-m1':
            kubectl('cordon ' + vmname)
            kubectl('drain --timeout=10s --delete-emptydir-data --ignore-daemonsets --force ' + vmname)
            kubectl('delete node ' + vmname)
            # remove the node password secret or a rebuilt node with this name gets rejected
            kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found')

        # destroy vm
        prox_destroy(vmid)

    kplan_tick()

# any other currently existing master vmid, or None if vmid is the only one up -
# kubectl always needs a live target and it cannot be the master being operated on
def other_master(vmid: int) -> int | None:
    vmids = list_kopsrox_vm()
    for candidate in (masterid, masterid + 1, masterid + 2):
        if candidate != vmid and candidate in vmids:
            return candidate
    return None

# forget a node's cluster registration ( node object + password secret ) via
# another master - etcd otherwise rejects a rejoining node reusing a name it
# still holds membership under ( the same trap k3s_remove_node works around
# for rebuilt nodes ). returns False if no other master is up to do it through
def k3s_forget_node(vmid: int) -> bool:
    via = other_master(vmid)
    if via is None:
        return False
    vmname = vmnames[vmid]
    kubectl(f'delete node {vmname} --ignore-not-found', via)
    kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found', via)
    return True

# remove cluster - leave master if restore = true
def k3s_rm_cluster() -> None:

    # list all kopsrox vm id's
    for vmid in sorted(list_kopsrox_vm(), reverse = True):

        # map hostname
        vmname = vmnames[vmid]

        # do not delete image or utility node
        if vmname == f'{cluster_name}-i0' or vmname == f'{cluster_name}-u1':
            continue

        # remove node from cluster and proxmox
        if vmname == f'{cluster_name}-m1':
            prox_destroy(vmid)
            kplan_tick()
        else:
            k3s_remove_node(vmid)

# best effort plan unit count for the progress bar - mirrors k3s_update_cluster
# units: 1 per missing node ( clone + prepare ), 1 per target k3s init/check,
# 1 for kubeconfig/token export when the master needs installing, 1 per removal
def cluster_plan_total() -> int:

    vmids = list_kopsrox_vm()

    # target nodes per the ini
    targets = [masterid]
    if masters == 3:
        targets += [masterid + 1, masterid + 2]
    workerid = masterid + 3
    targets += [workerid + count for count in range(1, workers + 1)]

    total = 0
    for target in targets:
        if target not in vmids:
            total += 1
        total += 1

    # kubeconfig / token export happens when the master actually installs
    if not conf_check_master_up:
        total += 1

    # removals - extra masters and anything past the last configured worker
    last_worker = workerid + workers
    for vmid in vmids:
        if masters == 1 and vmid in (masterid + 1, masterid + 2):
            total += 1
        if vmid > last_worker:
            total += 1

    return total

# builds or removes other nodes from the cluster as required per config
def k3s_update_cluster() -> None:
    kmsg('k3s_update-cluster', f'{cluster_name}/{k3s_version} - checking {masters} masters and {workers} workers', 'sys')

    # checks the master node
    k3s_init_node()

    # get list of running vms
    vmids = list_kopsrox_vm()

    # do we need to run any more masters
    if masters > 1:
        master_count = int(1)

        while ( master_count <=  2 ):

            # so eg 601 + 1 = 602 = m2
            slave_masterid = masterid + master_count
            slave_hostname = vmnames[slave_masterid]

            # existing server
            if slave_masterid not in vmids:
                clone(slave_masterid)

            # install k3s on slave and join master
            k3s_init_node(slave_masterid,'slave')

            # next possible master ( m3 )
            master_count = master_count + 1

    # check for extra masters
    if masters == 1:
        for vm in vmids:
            # is this required?
            vm = int(vm)

            # if vm is in the range of masterids
            if vm == (masterid + 1 ) or vm == (masterid + 2 ):
                # remove the vm
                k3s_remove_node(vm)

    # define default workerid ( -1 )
    workerid = masterid + 3

    # create new worker nodes per config
    if workers > 0:

        # first id in the loop
        worker_count: int = 1

        # cycle through possible workers
        while ( worker_count <= workers ):
            # calculate workerid
            workerid = masterid + 3 + worker_count

            # if existing vm with this id found
            if workerid not in vmids:
                clone(workerid)

            # checks worker has k3s installed first
            k3s_init_node(workerid,'worker')
            worker_count = worker_count + 1

    # remove extra workers
    for vm in vmids:
        if vm > workerid:
            kmsg('k3s_extra-worker', vmnames[vm])
            k3s_remove_node(vm)

    # display cluster info
    cluster_info()

# upgrade every live node to the k3s_version configured in the ini - masters
# one at a time first ( never more than one down, preserving etcd quorum ),
# then workers. re-runs the k3s installer live against each running node -
# same mechanism image_create() uses to bake a fresh image, just targeting
# an already-running node instead of the stopped template. the installer is
# idempotent ( skips the restart if the binary is already at this version )
def k3s_upgrade_cluster() -> None:
    kmsg('k3s_upgrade-cluster', f'{cluster_name} - upgrading to {k3s_version}', 'sys')

    # download the k3s install script if missing - same cached copy image_create() uses
    get_k3s_path = './lib/scripts/k3s.sh'
    if not os.path.isfile(get_k3s_path):
        kmsg('k3s_upgrade-cluster', 'downloading script from https://get.k3s.io...')
        try:
            dl_k3s = requests.get('https://get.k3s.io')
            open(get_k3s_path, 'wb').write(dl_k3s.content)
        except Exception:
            kabort('k3s_upgrade-cluster', 'unable to download get k3s script')
    installer = open(get_k3s_path).read()

    vmids = list_kopsrox_vm()

    # masters one at a time - never more than one down at once
    for vmid in (masterid, masterid + 1, masterid + 2):
        if vmid in vmids:
            k3s_upgrade_node(vmid, 'server', installer)

    # then workers
    for vmid in sorted(vmids):
        if vmid > masterid + 3:
            k3s_upgrade_node(vmid, 'agent', installer)

    kmsg('k3s_upgrade-cluster', f'{cluster_name} upgraded to {k3s_version}', 'sys')
    cluster_info()

# upgrade one already-running node - pushes the installer and re-runs it with
# the configured version ( no SKIP_START/SKIP_ENABLE this time - the
# installer restarts the service itself once it detects the binary changed )
def k3s_upgrade_node(vmid: int, role: str, installer: str) -> None:
    vmname = vmnames[vmid]
    with kstep('k3s_upgrade', f'upgrading {vmname} to {k3s_version}') as step:
        qa_write(vmid, '/root/k3s-upgrade.sh', installer, '755')
        qa_exec(vmid, f'INSTALL_K3S_VERSION={k3s_version} /root/k3s-upgrade.sh {role} > /k3s_upgrade.log 2>&1')
        qa_exec(vmid, 'rm -f /root/k3s-upgrade.sh')
        step.msg = f'waiting for {vmname} Ready'
        k3s_wait_ready(vmid, vmname)

# kubeconfig
def kubeconfig() -> None:

    # define filename
    kubeconfig = f'{cluster_name}.kubeconfig'
    # replace 127.0.0.1 with vip ip
    kconfig = qa_exec(masterid, 'cat /etc/rancher/k3s/k3s.yaml').replace('127.0.0.1', network_ip)
    with open(kubeconfig, 'w') as new_kubeconfig:
        new_kubeconfig.write(kconfig)
    kmsg('k3s_kubeconfig', f'saved {kubeconfig}')

# kubectl - vmid lets a caller route through a specific master ( eg when
# masterid itself is the node being operated on and has no working kubectl )
def kubectl(cmd: str, vmid: int = masterid) -> str:
    k3s_cmd = f'/usr/local/bin/kubectl {cmd} 2>&1'
    kcmd = qa_exec(vmid,k3s_cmd)
    return kcmd

# run k3s check config
def k3s_check_config() -> None:
    kmsg('k3s_check-config', 'checking k3s config')
    k3s_cmd = f'/usr/local/bin/k3s check-config'
    kcmd = qa_exec(masterid,k3s_cmd)
    print(kcmd)

# export k3s token - restore=True skips the password check. a restore reuses
# the saved token ( passed to --cluster-reset ), so the password stays the same;
# only the K10<ca-hash> prefix can differ, if the snapshot's restored CA differs
# from the one the saved token was last exported under. that is expected after a
# restore, not a different cluster reusing the name, so don't abort on it
def export_k3s_token(restore: bool = False) -> None:

    # define token file name
    token_name = f'{cluster_name}.k3stoken'
    # get masters token
    live_token = qa_exec(masterid, 'cat /var/lib/rancher/k3s/server/token')

    # check existing token ( get_k3s_token flags a stray line break / CR )
    saved_token = get_k3s_token()
    if saved_token is not None:

        # difference between live and local token
        if not saved_token == live_token:

            # passwords are different..
            if not restore and not saved_token.split(':')[3] == live_token.split(':')[3]:
                kabort('k3s_export-token', 'passwords different between live system and local token! exiting')

            # CA ( and after a restore, the whole token ) is different - expected
            kmsg('k3s_export-token', f'found: {token_name} updating')
            with open(token_name, 'w') as token_file:
                token_file.write(live_token)

        # existing token file matches live
        else:
            kmsg('k3s_export-token', f'found: {token_name} OK')

    # no token found so write new one
    else:
        with open(token_name, 'w') as token_file:
            token_file.write(live_token)
        kmsg('k3s_export-token', f'created: {token_name}')

# cluster info
def cluster_info() -> None:

    # live nodes in cluster
    cluster_info_vms = list_kopsrox_vm()

    # check m1 id exists
    if masterid not in cluster_info_vms:
        kabort('cluster_info', f'cluster {cluster_name} does not exist')

    kmsg(f'cluster_info', '', 'sys')
    curr_master = get_kube_vip_master()

    # for kopsrox vms
    for vmid in cluster_info_vms:
        if not cluster_id == vmid:
            hostname = vmnames[vmid]
            vmstatus = f'[{cluster_info_vms[vmid]}] {vmip(vmid)}/{network_mask}'
            if hostname == curr_master:
                vmstatus += f' vip {network_ip}/{network_mask}'
            kmsg(f'{hostname}_{vmid}', f'{vmstatus}')

    # fix this
    kmsg('kubectl_get-nodes', f'\n{kubectl("get nodes")}')

# reload kubevip
def reload_kubevip() -> str:
    return kubectl('-n kube-system rollout restart daemonset kubevip')

# return current vip master
def get_kube_vip_master() -> str:
    kubevip_q = f'get nodes --selector kube-vip.io/has-ip={network_ip}'
    kubevip_o = kubectl(kubevip_q)
    try:
        kubevip_m = kubevip_o.split()[5]
    except Exception:
        kubevip_m = ''
    return kubevip_m
