#!/usr/bin/env python3

import os
import re
import time

import requests

from kopsrox_artifacts import k3s_config, kopsrox_manifest
from kopsrox_config import (
    CLUSTER_CONFIG_OPTS,
    cluster_id,
    cluster_name,
    conf_check_master_up,
    config_hash,
    get_k3s_token,
    k3s_version,
    list_kopsrox_vm,
    masterid,
    masters,
    network_ip,
    network_mask,
    pve_run,
    vmip,
    vmnames,
    workers,
)
from kopsrox_proxmox import clone, internet_check, prox_destroy, qa_exec, qa_write
from kopsrox_kmsg import kabort, kmsg, kplan_tick, kstep


def k3s_check(vmid: int) -> bool:

    # quiet probe - kubectl may not exist yet, so never fail the exec
    get_node = qa_exec(masterid, f'/usr/local/bin/kubectl get node {vmnames[vmid]} 2>&1 || true')

    # word boundary - plain 'Ready' also matches 'NotReady'
    return bool(re.search(r'\bReady\b', get_node))

# baked-in unit per role - server covers master/slave/restore, agent is workers
def k3s_service(nodetype: str) -> str:
    return 'k3s-agent' if nodetype == 'worker' else 'k3s'

# write config.yaml and start the baked-in unit - no install at join time. token
# defaults to the saved file for determinism; pass '' to force a fresh identity
def k3s_join(vmid: int, nodetype: str, token: str | None = None) -> None:
    if token is None:
        token = get_k3s_token() or ''
    service = k3s_service(nodetype)
    qa_write(vmid, '/etc/rancher/k3s/config.yaml', k3s_config(nodetype, token))
    qa_exec(vmid, f'systemctl enable --now {service} > /k3s_{nodetype}_install.log 2>&1')

def k3s_wait_ready(vmid: int, vmname: str) -> None:
    wait: int = 20
    for count in range(wait):
        if k3s_check(vmid):
            return
        time.sleep(1)
    kabort('k3s_check', f'timed out after {wait}s for {vmname}')

def k3s_init_node(vmid: int = masterid, nodetype: str = 'master', snapshot: str | None = None) -> None:

    if nodetype not in ['master', 'slave', 'worker', 'restore']:
        kabort('k3s_init-node', f'{nodetype} invalid nodetype')

    internet_check(vmid)

    vmname = vmnames[vmid]

    if k3s_check(vmid):
        kmsg(f'k3s_{nodetype}', f'{vmname} Ready')
        kplan_tick()
        return

    if nodetype in ['master', 'worker', 'slave']:
        with kstep(f'k3s_{nodetype}', f'starting k3s {k3s_version} on {vmname}') as step:
            k3s_join(vmid, nodetype)
            step.msg = f'waiting for {vmname} Ready'
            k3s_wait_ready(vmid, vmname)
        kplan_tick()

    # bootstrap a throwaway master ( etcd-snapshot ls needs a live api server ),
    # then reset+restore. the SAVED token is mandatory - it is the only key that
    # decrypts the snapshot's bootstrap data
    if nodetype == 'restore':
        token = get_k3s_token()
        if not token:
            kabort('k3s_restore', f'{cluster_name}.k3stoken is required to restore - it must be '
                   f'the token the target snapshot was taken with, or the bootstrap data cannot be decrypted')

        with kstep('k3s_restore', f'bootstrapping {vmname} to read snapshots') as step:
            k3s_join(vmid, 'master', token='')
            step.msg = f'waiting for {vmname} Ready'
            k3s_wait_ready(vmid, vmname)

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

        # bare name only - an absolute path makes k3s double the snapshot dir
        snapshot = snapshot.rsplit('/', 1)[-1]

        kmsg('k3s_restore', f'restoring {vmname} from snapshot {snapshot}', 'sys')

        with kstep('k3s_restore', f'resetting etcd from {snapshot}') as step:
            qa_exec(vmid, 'systemctl stop k3s && rm -rf /var/lib/rancher/')

            # unchecked, a failure here silently boots an empty cluster instead
            rc = qa_exec(vmid, f'/usr/local/bin/k3s server --cluster-reset '
                               f'--cluster-reset-restore-path={snapshot} --token={token} '
                               f'> /k3s_restore.log 2>&1; echo RC=$?').rsplit('RC=', 1)[-1].strip()
            restore_log = qa_exec(vmid, 'cat /k3s_restore.log')

            # k3s <=1.34 doubles the path decompressing a .zip - unzip and retry
            if (rc != '0' or re.search('level=fatal', restore_log)) and snapshot.endswith('.zip'):
                snapdir = '/var/lib/rancher/k3s/server/db/snapshots'
                plain = qa_exec(vmid, f'rm -rf /tmp/ksnap && mkdir -p /tmp/ksnap && '
                                      f'unzip -o {snapdir}/{snapshot} -d /tmp/ksnap > /dev/null 2>&1 && '
                                      f'echo /tmp/ksnap/$(basename {snapshot} .zip)')
                rc = qa_exec(vmid, f'/usr/local/bin/k3s server --cluster-reset --etcd-s3=false '
                                   f'--cluster-reset-restore-path={plain} --token={token} '
                                   f'> /k3s_restore.log 2>&1; echo RC=$?').rsplit('RC=', 1)[-1].strip()
                restore_log = qa_exec(vmid, 'cat /k3s_restore.log')

            if rc != '0' or re.search('level=fatal', restore_log):
                kabort('k3s_restore', f'snapshot restore failed ( rc={rc} ) - /k3s_restore.log on {vmname}:\n{restore_log}')

            # the reset wiped /var/lib/rancher - reapply or the vip never returns
            qa_exec(vmid, 'mkdir -p /var/lib/rancher/k3s/server/manifests')
            qa_write(vmid, f'/var/lib/rancher/k3s/server/manifests/kopsrox-{cluster_name}.yaml', kopsrox_manifest())
            qa_exec(vmid, 'systemctl start k3s')
            step.msg = f'waiting for {vmname} Ready'
            k3s_wait_ready(vmid, vmname)
        kplan_tick()

    if nodetype in ['master', 'restore']:
        with kstep('k3s_export', 'kubeconfig + token'):
            kubeconfig()
            export_k3s_token(restore=(nodetype == 'restore'))
        kplan_tick()

def k3s_remove_node(vmid: int) -> None:

    vmname = vmnames[vmid]

    with kstep('k3s_remove-node', vmname):
        if vmname != f'{cluster_name}-m1':
            kubectl('cordon ' + vmname)
            kubectl('drain --timeout=10s --delete-emptydir-data --ignore-daemonsets --force ' + vmname)
            kubectl('delete node ' + vmname)
            # or a rebuilt node reusing this name gets rejected
            kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found')

        prox_destroy(vmid)

    kplan_tick()

# another live master to run kubectl through - never the one being operated on
def other_master(vmid: int) -> int | None:
    vmids = list_kopsrox_vm()
    for candidate in (masterid, masterid + 1, masterid + 2):
        if candidate != vmid and candidate in vmids:
            return candidate
    return None

# drop a node's registration via another master, or etcd rejects a rejoin
# reusing the name. False if no other master is up
def k3s_forget_node(vmid: int) -> bool:
    via = other_master(vmid)
    if via is None:
        return False
    vmname = vmnames[vmid]
    kubectl(f'delete node {vmname} --ignore-not-found', via)
    kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found', via)
    return True

# remove cluster - leaves the master when restore=True
def k3s_rm_cluster() -> None:

    for vmid in sorted(list_kopsrox_vm(), reverse = True):

        vmname = vmnames[vmid]

        if vmname == f'{cluster_name}-i0' or vmname == f'{cluster_name}-u1':
            continue

        if vmname == f'{cluster_name}-m1':
            prox_destroy(vmid)
            kplan_tick()
        else:
            k3s_remove_node(vmid)

def cluster_plan_total() -> int:

    vmids = list_kopsrox_vm()

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

    if not conf_check_master_up:
        total += 1

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

    k3s_init_node()

    vmids = list_kopsrox_vm()

    if masters > 1:
        master_count = int(1)

        while ( master_count <=  2 ):

            slave_masterid = masterid + master_count
            slave_hostname = vmnames[slave_masterid]

            if slave_masterid not in vmids:
                clone(slave_masterid)

            k3s_init_node(slave_masterid,'slave')

            master_count = master_count + 1

    if masters == 1:
        for vm in vmids:
            vm = int(vm)

            if vm == (masterid + 1 ) or vm == (masterid + 2 ):
                k3s_remove_node(vm)

    workerid = masterid + 3

    if workers > 0:

        worker_count: int = 1

        while ( worker_count <= workers ):
            workerid = masterid + 3 + worker_count

            if workerid not in vmids:
                clone(workerid)

            k3s_init_node(workerid,'worker')
            worker_count = worker_count + 1

    for vm in vmids:
        if vm > workerid:
            kmsg('k3s_extra-worker', vmnames[vm])
            k3s_remove_node(vm)

    # cluster-config drift baseline, in the otherwise unused m1 description
    pve_run(['qm', 'set', str(masterid), '--description',
             f'{masterid}:{vmnames[masterid]}:{vmip(masterid)}\nconfig_hash: {config_hash(CLUSTER_CONFIG_OPTS)}'])

    cluster_info()

# re-run the installer on every live node; masters first, for etcd quorum
def k3s_upgrade_cluster() -> None:
    kmsg('k3s_upgrade-cluster', f'{cluster_name} - upgrading to {k3s_version}', 'sys')

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

    # one at a time - never more than one master down at once
    for vmid in (masterid, masterid + 1, masterid + 2):
        if vmid in vmids:
            k3s_upgrade_node(vmid, 'server', installer)

    for vmid in sorted(vmids):
        if vmid > masterid + 3:
            k3s_upgrade_node(vmid, 'agent', installer)

    kmsg('k3s_upgrade-cluster', f'{cluster_name} upgraded to {k3s_version}', 'sys')
    cluster_info()

# no SKIP_START/SKIP_ENABLE - the installer restarts the service itself
def k3s_upgrade_node(vmid: int, role: str, installer: str) -> None:
    vmname = vmnames[vmid]
    with kstep('k3s_upgrade', f'upgrading {vmname} to {k3s_version}') as step:
        qa_write(vmid, '/root/k3s-upgrade.sh', installer, '755')
        qa_exec(vmid, f'INSTALL_K3S_VERSION={k3s_version} /root/k3s-upgrade.sh {role} > /k3s_upgrade.log 2>&1')
        qa_exec(vmid, 'rm -f /root/k3s-upgrade.sh')
        step.msg = f'waiting for {vmname} Ready'
        k3s_wait_ready(vmid, vmname)

def kubeconfig() -> None:

    kubeconfig = f'{cluster_name}.kubeconfig'
    kconfig = qa_exec(masterid, 'cat /etc/rancher/k3s/k3s.yaml').replace('127.0.0.1', network_ip)
    with open(kubeconfig, 'w') as new_kubeconfig:
        new_kubeconfig.write(kconfig)
    kmsg('k3s_kubeconfig', f'saved {kubeconfig}')

# vmid routes through a specific master, for when masterid is the node itself
def kubectl(cmd: str, vmid: int = masterid) -> str:
    k3s_cmd = f'/usr/local/bin/kubectl {cmd} 2>&1'
    kcmd = qa_exec(vmid,k3s_cmd)
    return kcmd

def k3s_check_config() -> None:
    kmsg('k3s_check-config', 'checking k3s config')
    k3s_cmd = f'/usr/local/bin/k3s check-config'
    kcmd = qa_exec(masterid,k3s_cmd)
    print(kcmd)

# restore=True skips the password check - only the K10<ca-hash> prefix changes
def export_k3s_token(restore: bool = False) -> None:

    token_name = f'{cluster_name}.k3stoken'
    live_token = qa_exec(masterid, 'cat /var/lib/rancher/k3s/server/token')

    saved_token = get_k3s_token()
    if saved_token is not None:

        if not saved_token == live_token:

            if not restore and not saved_token.split(':')[3] == live_token.split(':')[3]:
                kabort('k3s_export-token', 'passwords different between live system and local token! exiting')

            # CA differs - expected after a restore
            kmsg('k3s_export-token', f'found: {token_name} updating')
            with open(token_name, 'w') as token_file:
                token_file.write(live_token)

        else:
            kmsg('k3s_export-token', f'found: {token_name} OK')

    else:
        with open(token_name, 'w') as token_file:
            token_file.write(live_token)
        kmsg('k3s_export-token', f'created: {token_name}')

def cluster_info() -> None:

    cluster_info_vms = list_kopsrox_vm()

    if masterid not in cluster_info_vms:
        kabort('cluster_info', f'cluster {cluster_name} does not exist')

    kmsg(f'cluster_info', '', 'sys')
    curr_master = get_kube_vip_master()

    for vmid in cluster_info_vms:
        if not cluster_id == vmid:
            hostname = vmnames[vmid]
            vmstatus = f'[{cluster_info_vms[vmid]}] {vmip(vmid)}/{network_mask}'
            if hostname == curr_master:
                vmstatus += f' vip {network_ip}/{network_mask}'
            kmsg(f'{hostname}_{vmid}', f'{vmstatus}')

    kmsg('kubectl_get-nodes', f'\n{kubectl("get nodes")}')

def reload_kubevip() -> str:
    return kubectl('-n kube-system rollout restart daemonset kubevip')

def get_kube_vip_master() -> str:
    kubevip_q = f'get nodes --selector kube-vip.io/has-ip={network_ip}'
    kubevip_o = kubectl(kubevip_q)
    try:
        kubevip_m = kubevip_o.split()[5]
    except Exception:
        kubevip_m = ''
    return kubevip_m
