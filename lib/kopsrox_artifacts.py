#!/usr/bin/env python3

# cluster artifact generators - shared by verb_image ( writes local copies )
# and node_prepare ( pushes config-fresh copies into nodes via the guest agent )
from kopsrox_config import *

# k3s manifest - kubevip + traefik + cloud controller + csi
def kopsrox_manifest():

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
  return(manifest)

# k3s server config file ( /etc/rancher/k3s/config.yaml )
def k3s_server_config():
  server_config = f'''\
disable-cloud-controller: true
tls-san:
  - {network_ip}
  - {vmip(masterid)}
  - {vmip(masterid + 1)}
  - {vmip(masterid + 2)}
write-kubeconfig-mode: 0644
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
etcd-snapshot-compress: true'''

  # handle s3 region
  if region_string != '':
    server_config += f'''
etcd-s3-region: {region_string}'''
  return(server_config)

# in-node k3s install script ( /root/scripts/kopsrox.sh )
def kopsrox_sh():
  k3s_ver = f'cat /root/scripts/k3s.sh | INSTALL_K3S_VERSION={k3s_version}'
  k3s_opt = f'--kubelet-arg --provider-id=proxmox://{cluster_name}/$2'
  k3s_server = f'--server https://{network_ip}:6443'
  k3s_master = f'{k3s_ver} sh -s - server --cluster-init {k3s_opt}'
  k3s_slave = f'{k3s_ver} sh -s - server {k3s_server} {k3s_opt}'
  k3s_worker = f'rm -rf /etc/rancher/k3s/* && {k3s_ver} sh -s - agent {k3s_server} {k3s_opt}'
  return(f'''\
#!/usr/bin/env bash
if [[ ! "$1" ]] then
echo 'command not passed'
exit
fi

if [[ ! "$2" ]] then
echo 'vmid not passed'
fi

if [[ "$3" ]] then
token_command="--token $3"
fi

if [[ "$1" == "master" ]] then
{k3s_master} $token_command
exit
fi

if [[ "$1" == "slave" ]] then
{k3s_slave} $token_command
exit
fi

if [[ "$1" == "worker" ]] then
{k3s_worker} $token_command
exit
fi

if [[ "$1" == "latest" ]] then
{k3s_master} $token_command && /usr/local/bin/k3s etcd-snapshot ls 2>&1 && systemctl stop k3s && rm -rf /var/lib/rancher
exit
fi

if [[ "$1" == "restore" ]] then
{k3s_master} $token_command && systemctl stop k3s && rm -rf /var/lib/rancher && /usr/local/bin/k3s server --cluster-reset --cluster-reset-restore-path=$2 $token_command 2>&1 && systemctl start k3s
exit
fi

''')
