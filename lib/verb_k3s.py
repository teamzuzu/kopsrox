#!/usr/bin/env python3

from kopsrox_k3s import (
    export_k3s_token,
    k3s_check_config,
    kubeconfig,
    kubectl,
    reload_kubevip,
)
from kopsrox_kmsg import kmsg


def run(cmd: str, arg: str | None = None) -> None:

    # k3s token
    if cmd == 'export-token':
        export_k3s_token()

    # export kubeconfig to file
    if cmd == 'kubeconfig':
        kubeconfig()

    # check k3s config
    if cmd == 'check-config':
        k3s_check_config()

    # reload kubevip
    if cmd == 'reload-kubevip':
        reload_kubevip()

    # kubectl
    if cmd == 'kubectl':

        # single quoted command string passed as arg
        kcmd = arg

        # run command and show output
        kmsg('kubectl_cmd', kcmd, 'sys')
        print(kubectl(kcmd))
