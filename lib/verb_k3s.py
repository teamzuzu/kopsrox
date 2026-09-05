#!/usr/bin/env python3

from kopsrox_k3s import (
    export_k3s_token,
    k3s_check_config,
    k3s_upgrade_cluster,
    kubeconfig,
    kubectl,
    reload_kubevip,
)
from kopsrox_kmsg import kmsg


def run(cmd: str, arg: str | None = None) -> None:

    if cmd == 'export-token':
        export_k3s_token()

    if cmd == 'kubeconfig':
        kubeconfig()

    if cmd == 'check-config':
        k3s_check_config()

    if cmd == 'reload-kubevip':
        reload_kubevip()

    # upgrade live nodes, then rebuild the image so future clones match
    if cmd == 'upgrade':
        k3s_upgrade_cluster()
        from verb_image import image_create
        image_create()

    if cmd == 'kubectl':

        kcmd = arg

        kmsg('kubectl_cmd', kcmd, 'sys')
        print(kubectl(kcmd))
