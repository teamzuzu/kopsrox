#!/usr/bin/env python3

import re

from kopsrox_config import bucket, cluster_name, get_k3s_token, masterid, s3_endpoint, vms
from kopsrox_kmsg import kabort, kmsg
from kopsrox_proxmox import qa_exec


# run k3s s3 command passed
def s3_run(cmd: str, s3cmd: str) -> str:
    kname = f'etcd_{cmd}'

    # run the command ( 2>&1 required )
    k3s_run = f'k3s etcd-snapshot {s3cmd} 2>&1'
    s3_out = qa_exec(masterid, k3s_run)

    # look for fatal error in output
    if re.search('level=fatal', s3_out):
        kabort(f'{kname}-s3run', f'\n {s3_out}')

    # return command outpit
    return s3_out


# list images in s3 storage
def list_snapshots(cmd: str) -> str:

    # run s3 ls and create a list per line
    ls = s3_run(cmd, 'ls').split('\n')

    # images string
    images = ''

    # for each image in the sorted list
    for line in sorted(ls):

        # map filename
        s3_file = line.split()[0]

        # if cluster name matches the s3 line append to the images string
        if re.search(f'kopsrox-{cluster_name}', s3_file) and re.search('s3', line):
            images += f'{s3_file} - {line.split()[3]}\n'

    # return images string
    return images.strip()


# print s3List
def s3_list(cmd: str, snapshots: str) -> None:
    kmsg('etcd_repo', f'{s3_endpoint}/{bucket}\n{snapshots}')


# module-level prologue for every etcd command - master check, token check,
# s3 connection test - returns the current list of snapshots
def _etcd_checks(cmd: str) -> str:
    kname = f'etcd_{cmd}'

    # check master is running / exists
    try:
        vms[masterid]
    except Exception:
        kabort(f'{kname}-check', 'cluster does not exist')

    # check token
    try:
        get_k3s_token()
    except Exception:
        kabort(f'{kname}-check', 'problem with k3s token')

    # test connection to s3 by getting list of snapshots
    try:
        snapshots = list_snapshots(cmd)
    except Exception:
        kabort(f'{kname}-check', 'error getting data from s3 repo')

    return snapshots


def run(cmd: str, arg: str | None = None) -> None:
    kname = f'etcd_{cmd}'

    snapshots = _etcd_checks(cmd)

    # s3 prune
    if cmd == 'prune':
        kmsg(f'{kname}-prune', (f'{s3_endpoint}/{bucket}\n' + s3_run(cmd, 'prune --name kopsrox')), 'sys')
        exit(0)

    # snapshot
    if cmd == 'snapshot':

        # run save
        snapout = s3_run(cmd, 'save --name kopsrox').split('\n')
        last_line = ''
        for snap_out in snapout:
            if not re.search(' level=warning msg="Unknown flag', snap_out):
                if last_line != snap_out:
                    kmsg(kname, snap_out, 'sys')
                    last_line = snap_out

        # list snapshots
        snapshots = list_snapshots(cmd)
        s3_list(cmd, snapshots)

    # list
    if cmd == 'list':
        s3_list(cmd, snapshots)
        exit(0)
