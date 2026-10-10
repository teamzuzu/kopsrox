#!/usr/bin/env python3

import re

from kopsrox_config import (bucket, cluster_id, cluster_name, config_drift, disc_vms, kernel_notice,
                            masterid, microvm_release_notice, proxmox_storage, s3_endpoint, vmnames)
from kopsrox_kmsg import kmsg
from kopsrox_proxmox import qa_exec


# init() already aborted on a bad ini / missing storage, so reaching here is the pass
def config_check() -> None:
    kname = 'config_'

    kmsg(f'{kname}ini', 'kopsrox.ini valid', 'info')
    kmsg(f'{kname}storage', f'{proxmox_storage} found', 'info')

    if cluster_id in disc_vms:
        kmsg(f'{kname}image', f'{vmnames[cluster_id]} present', 'info')
    else:
        kmsg(f'{kname}image', f'{cluster_name} image not found - run "image create"', 'sys')

    drift = config_drift()
    for line in drift:
        kmsg(f'{kname}drift', line, 'sys')
    if not drift:
        kmsg(f'{kname}drift', 'kopsrox.ini matches the built image and cluster', 'info')

    s3_check(kname)
    microvm_release_notice(f'{kname}microvm')
    kernel_notice(f'{kname}kernel')


# the schema proves s3 is filled in, not that it works. only the master can prove
# that - "etcd-snapshot ls" exercises endpoint, credentials and bucket together
def s3_check(kname: str) -> None:
    if disc_vms.get(masterid, {}).get('status') != 'running':
        kmsg(f'{kname}s3', f'{s3_endpoint}/{bucket} - not verified ( no running master )')
        return

    # qa_exec defaults to a 600s timeout - a blackholed endpoint would hang the check
    out = qa_exec(masterid, 'k3s etcd-snapshot ls 2>&1', timeout = 30, fatal = False)
    fatal = [line for line in out.splitlines() if re.search('level=fatal', line)]
    if not out.strip():
        kmsg(f'{kname}s3', f'{s3_endpoint}/{bucket} - check timed out or the agent did not answer', 'err')
        return
    if fatal:
        kmsg(f'{kname}s3', f'{s3_endpoint}/{bucket} unreachable - etcd snapshots and cluster '
             f'restore will fail\n{fatal[0]}', 'err')
        return
    kmsg(f'{kname}s3', f'{s3_endpoint}/{bucket} reachable')


def run(cmd: str, arg: str | None = None) -> None:

    if cmd == 'check':
        config_check()
