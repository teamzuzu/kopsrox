#!/usr/bin/env python3

# cluster artifact generators - shared by verb_image ( writes local inspection
# copies ) and kopsrox_k3s.k3s_join ( pushes config-fresh copies into nodes at
# join time via the guest agent )
from kopsrox_config import (
    access_key,
    access_secret,
    bucket,
    cluster_name,
    masterid,
    network_ip,
    region_string,
    s3_endpoint,
    vmip,
)


# k3s manifest - kubevip + traefik + cloud controller + csi
def kopsrox_manifest() -> str:

    # generate kubevip manifest
    manifest = open('./lib/manifests/kubevip.yaml', 'r').read().replace('KOPSROX_IP', network_ip).strip()

    # generate traefik helm config
    manifest += f'''
---
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    service:
      spec:
        loadBalancerIP: "{network_ip}"'''

    # no proxmox cloud controller or csi driver on microvm
    # - the guest has no dmi so the ccm smbios uuid check can never pass
    # - the csi driver needs disk hotplug which pve-microvm does not support yet
    # storage is provided by the k3s local-path provisioner instead
    return manifest

# k3s config file ( /etc/rancher/k3s/config.yaml ) - role-aware since the
# systemd units baked into the image at build time carry no node-specific
# flags ( see verb_image.py ); every join-time flag k3s would otherwise take
# on the command line is supplied here instead, per k3s's own equivalence
# between config.yaml keys and CLI flags
def k3s_config(nodetype: str, vmid: int, token: str = '') -> str:
    kubelet_arg = f'"provider-id=proxmox://{cluster_name}/{vmid}"'

    # worker ( agent role ) - no etcd, no cloud-controller-manager, no tls-san
    if nodetype == 'worker':
        return f'''\
server: https://{network_ip}:6443
token: {token}
kubelet-arg:
  - {kubelet_arg}'''

    # master bootstraps its own cluster - slave joins an existing one. if a
    # token is passed for master ( eg a saved token from a prior cluster of
    # the same name ) pin cluster-init to it, so recreating a cluster is
    # deterministic rather than minting a new token every time. callers that
    # need a genuinely fresh identity ( k3s_join()'s restore bootstrap, ahead
    # of --cluster-reset-restore-path ) pass an empty token instead - pinning
    # a stale token there conflicts with the fresh CA cluster-reset generates
    if nodetype == 'master':
        join_config = 'cluster-init: true'
        if token:
            join_config += f'\ntoken: {token}'
    else:
        join_config = f'server: https://{network_ip}:6443\ntoken: {token}'

    region_config = ''
    if region_string != '':
        region_config = f'\netcd-s3-region: {region_string}'

    return f'''\
{join_config}
kubelet-arg:
  - {kubelet_arg}
disable-cloud-controller: true
tls-san:
  - {network_ip}
  - {vmip(masterid)}
  - {vmip(masterid + 1)}
  - {vmip(masterid + 2)}
write-kubeconfig-mode: "0644"
embedded-registry: true
disable:
  - servicelb
etcd-s3: true
etcd-disable-snapshot: true
etcd-snapshot-retention: 7
etcd-s3-endpoint: {s3_endpoint}
etcd-s3-access-key: {access_key}
etcd-s3-secret-key: {access_secret}
etcd-s3-bucket: {bucket}
etcd-s3-skip-ssl-verify: true
etcd-snapshot-compress: true{region_config}'''
