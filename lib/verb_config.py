#!/usr/bin/env python3

from kopsrox_config import (cluster_id, cluster_name, config_drift, disc_vms, kernel_notice,
                            microvm_release_notice, proxmox_storage, vmnames)
from kopsrox_kmsg import kmsg


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

    microvm_release_notice(f'{kname}microvm')
    kernel_notice(f'{kname}kernel')


def run(cmd: str, arg: str | None = None) -> None:

    if cmd == 'check':
        config_check()
