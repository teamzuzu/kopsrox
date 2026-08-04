#!/usr/bin/env python3

# cluster artifact generators - shared by verb_image ( writes local inspection
# copies ) and kopsrox_k3s.k3s_join ( pushes config-fresh copies into nodes at
# join time via the guest agent )
from kopsrox_config import (
    access_key,
    access_secret,
    bucket,
    kubelet_args,
    masterid,
    network_ip,
    nfs_path,
    nfs_server,
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


    # optional external-nfs backed 'nfs' storageclass ( opt-in via nfs_server )
    # - nfs-subdir-external-provisioner: one deployment + storageclass, a
    #   subdirectory per pv under a single export. needs no disk hotplug so the
    #   microvm csi limitation above does not apply. the kopsrox kernel bakes in
    #   the nfs client ( CONFIG_NFS_FS/V4 ) and nfs-common is a default package
    # - not annotated is-default-class, so local-path stays the cluster default;
    #   pods opt in with storageClassName: nfs
    # - archiveOnDelete true renames the backing dir to archived-* on pvc delete
    #   rather than purging it, so data survives an accidental delete
    if nfs_server != '':
        manifest += kopsrox_nfs_manifest()

    return manifest

# nfs-subdir-external-provisioner artifacts - appended to the cluster manifest
# only when nfs_server is configured ( see kopsrox_manifest above )
def kopsrox_nfs_manifest() -> str:
    return f'''
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: nfs-client-provisioner
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: nfs-client-provisioner-runner
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["persistentvolumes"]
    verbs: ["get", "list", "watch", "create", "delete"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "watch", "update"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: run-nfs-client-provisioner
subjects:
  - kind: ServiceAccount
    name: nfs-client-provisioner
    namespace: kube-system
roleRef:
  kind: ClusterRole
  name: nfs-client-provisioner-runner
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: leader-locking-nfs-client-provisioner
  namespace: kube-system
rules:
  - apiGroups: [""]
    resources: ["endpoints"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: leader-locking-nfs-client-provisioner
  namespace: kube-system
subjects:
  - kind: ServiceAccount
    name: nfs-client-provisioner
    namespace: kube-system
roleRef:
  kind: Role
  name: leader-locking-nfs-client-provisioner
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nfs-client-provisioner
  namespace: kube-system
  labels:
    app: nfs-client-provisioner
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: nfs-client-provisioner
  template:
    metadata:
      labels:
        app: nfs-client-provisioner
    spec:
      serviceAccountName: nfs-client-provisioner
      containers:
        - name: nfs-client-provisioner
          image: registry.k8s.io/sig-storage/nfs-subdir-external-provisioner:v4.0.2
          volumeMounts:
            - name: nfs-client-root
              mountPath: /persistentvolumes
          env:
            - name: PROVISIONER_NAME
              value: k8s-sigs.io/nfs-subdir-external-provisioner
            - name: NFS_SERVER
              value: {nfs_server}
            - name: NFS_PATH
              value: {nfs_path}
      volumes:
        - name: nfs-client-root
          nfs:
            server: {nfs_server}
            path: {nfs_path}
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs
provisioner: k8s-sigs.io/nfs-subdir-external-provisioner
reclaimPolicy: Delete
parameters:
  archiveOnDelete: "true"'''

# k3s config file ( /etc/rancher/k3s/config.yaml ) - role-aware since the
# systemd units baked into the image at build time carry no node-specific
# flags ( see verb_image.py ); every join-time flag k3s would otherwise take
# on the command line is supplied here instead, per k3s's own equivalence
# between config.yaml keys and CLI flags
def k3s_config(nodetype: str, token: str = '') -> str:
    # optional kubelet args from the ini ( kubelet_args, comma separated ) -
    # rendered as a kubelet-arg: yaml list, or omitted entirely when blank.
    # there is deliberately no provider-id here: it only mattered to the proxmox
    # ccm/csi ( both dropped - see CLAUDE.md ), and k3s defaults providerID to
    # k3s://<nodename> which is fine with no cloud provider
    kubelet_block = ''
    args = [a.strip() for a in kubelet_args.split(',') if a.strip()]
    if args:
        kubelet_block = '\nkubelet-arg:\n' + '\n'.join(f'  - "{a}"' for a in args)

    # worker ( agent role ) - no etcd, no cloud-controller-manager, no tls-san
    if nodetype == 'worker':
        return f'''\
server: https://{network_ip}:6443
token: {token}{kubelet_block}'''

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
{join_config}{kubelet_block}
tls-san:
  - {network_ip}
  - {vmip(masterid)}
  - {vmip(masterid + 1)}
  - {vmip(masterid + 2)}
write-kubeconfig-mode: "0644"
embedded-registry: true
disable:
  - servicelb
disable-network-policy: true
etcd-s3: true
etcd-disable-snapshots: true
etcd-snapshot-retention: 7
etcd-s3-endpoint: {s3_endpoint}
etcd-s3-access-key: {access_key}
etcd-s3-secret-key: {access_secret}
etcd-s3-bucket: {bucket}
etcd-s3-skip-ssl-verify: true
etcd-snapshot-compress: true{region_config}'''
