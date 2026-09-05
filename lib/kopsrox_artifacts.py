#!/usr/bin/env python3

# cluster artifact generators - local inspection copies, and the real files
# pushed into nodes at join time
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


def kopsrox_manifest() -> str:

    manifest = open('./lib/manifests/kubevip.yaml', 'r').read().replace('KOPSROX_IP', network_ip).strip()

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


    # optional nfs storageclass - not the default, so pods opt in explicitly
    if nfs_server != '':
        manifest += kopsrox_nfs_manifest()

    return manifest

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

# registries.yaml. embedded-registry: true only starts the Spegel mesh - nothing
# mirrors until listed under mirrors: here. needed on servers AND agents, so it
# is baked into the image rather than pushed per join
def k3s_registries() -> str:
    return 'mirrors:\n  "*":\n'


# config.yaml - the baked-in units carry no flags, so every flag lives here
def k3s_config(nodetype: str, token: str = '') -> str:
    # omitted entirely when blank. no provider-id - the dropped ccm/csi is gone
    kubelet_block = ''
    args = [a.strip() for a in kubelet_args.split(',') if a.strip()]
    if args:
        kubelet_block = '\nkubelet-arg:\n' + '\n'.join(f'  - "{a}"' for a in args)

    if nodetype == 'worker':
        return f'''\
server: https://{network_ip}:6443
token: {token}{kubelet_block}'''

    # pinning cluster-init to a saved token makes recreating a cluster
    # deterministic; the restore bootstrap passes '' for a fresh CA
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
etcd-snapshot-compress: false{region_config}'''  # compressed snapshots hit a k3s <=1.34 restore path-doubling bug
