#!/usr/bin/env python3

import re
import shlex

from kopsrox_config import bucket, cluster_name, get_k3s_token, masterid, s3_endpoint, vms
from kopsrox_kmsg import kabort, kmsg
from kopsrox_proxmox import qa_exec


# run k3s s3 command passed
def s3_run(cmd: str, s3cmd: str) -> str:
    kname = f'etcd_{cmd}'

    # 2>&1 required
    k3s_run = f'k3s etcd-snapshot {s3cmd} 2>&1'
    s3_out = qa_exec(masterid, k3s_run)

    if re.search('level=fatal', s3_out):
        kabort(f'{kname}-s3run', f'\n {s3_out}')

    return s3_out


def list_snapshots(cmd: str) -> str:
    snapshots = []

    for line in sorted(s3_run(cmd, 'ls').splitlines()):
        fields = line.split()
        if len(fields) != 4:
            continue

        name, location, size, created = fields
        if name.startswith(f'kopsrox-{cluster_name}-') and location.startswith('s3://'):
            size_bytes = int(size)
            size_mb = '<0.01 MB' if 0 < size_bytes < 10_000 else f'{size_bytes / 1_000_000:,.2f} MB'
            snapshots.append((name, created, size_mb))

    columns = [('Snapshot', 'Created', 'Size'), *snapshots]
    name_width = max(len(name) for name, _, _ in columns)
    created_width = max(len(created) for _, created, _ in columns)
    size_width = max(len(size) for _, _, size in columns)
    return '\n'.join(
        f'{name:<{name_width}}  {created:<{created_width}}  {size:>{size_width}}'
        for name, created, size in columns
    )


def s3_list(cmd: str, snapshots: str) -> None:
    kmsg('etcd_repo', f'{s3_endpoint}/{bucket}\n{snapshots}')


# prologue for every etcd command - returns the current list of snapshots
def _etcd_checks(cmd: str) -> str:
    kname = f'etcd_{cmd}'

    try:
        vms[masterid]
    except Exception:
        kabort(f'{kname}-check', 'cluster does not exist')

    try:
        get_k3s_token()
    except Exception:
        kabort(f'{kname}-check', 'problem with k3s token')

    try:
        snapshots = list_snapshots(cmd)
    except Exception:
        kabort(f'{kname}-check', 'error getting data from s3 repo')

    return snapshots


def run(cmd: str, arg: str | None = None) -> None:
    kname = f'etcd_{cmd}'

    snapshots = _etcd_checks(cmd)

    if cmd == 'prune':
        # K3s matches a name prefix across the shared S3 repository. Include
        # this cluster's master-node prefix, as save appends the node name.
        prefix = shlex.quote(f'kopsrox-{cluster_name}-m')
        kmsg(f'{kname}-prune', (f'{s3_endpoint}/{bucket}\n' + s3_run(cmd, f'prune --name {prefix}')), 'sys')
        exit(0)

    if cmd == 'snapshot':

        snapout = s3_run(cmd, 'save --name kopsrox').split('\n')
        last_line = ''
        for snap_out in snapout:
            if not re.search(' level=warning msg="Unknown flag', snap_out):
                if last_line != snap_out:
                    kmsg(kname, snap_out, 'sys')
                    last_line = snap_out

        snapshots = list_snapshots(cmd)
        s3_list(cmd, snapshots)

    if cmd == 'list':
        s3_list(cmd, snapshots)
        exit(0)
