#!/usr/bin/env python3

# functions
from kopsrox_config import *
from kopsrox_proxmox import prox_task, prox_destroy

# define command
cmd = sys.argv[2]
kname = 'image_'

# create image
if cmd == 'create':

  cloud_image = cloud_image_url.split('/')[-1]
  kmsg(f'{kname}create', f'{cluster_name}-i0 template based on {cloud_image}', 'sys')

  # download k3s.sh
  get_k3s_path = './lib/scripts/k3s.sh'
  if not os.path.isfile(get_k3s_path):
    kmsg(f'{kname}wget', f'https://get.k3s.io')
    try:
      wget.download('https://get.k3s.io', get_k3s_path)
      print()
    except:
      kmsg(f'{kname}check', f'unable to download get k3s script', 'err')

  # check if image already exists
  if os.path.isfile(cloud_image):
    kmsg(f'{kname}check', f'{cloud_image} already exists - removing','sys')
    try:
      os.remove(cloud_image)
    except:
      kmsg(f'{kname}check', f'{cloud_image} cannot delete', 'err')
      exit(0)

  # check img can be downloaded
  try:
    kmsg(f'{kname}wget', f'{cloud_image_url}')
    wget.download(cloud_image_url)
    print()
  except:
    kmsg(f'{kname}check', f'unable to download {cloud_image_url}', 'err')
    exit(0)

  # open kopsrox manifest
  kopsrox_yaml = f'./lib/manifests/kopsrox-{cluster_name}.yaml'
  kopsrox_manifest = open(kopsrox_yaml, 'w')

  # generate kubevip manifest
  kv_manifest = open('./lib/manifests/kubevip.yaml', 'r').read().replace('KOPSROX_IP', network_ip).strip()
  kopsrox_manifest.write(kv_manifest)

  # generate traefik helm config
  traefik_conf = f'''
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
  kopsrox_manifest.write(traefik_conf)

  # define common secret section for sergelogvinov's helm charts
  controller_common = f'''
    config:
      clusters:
        - url: https://{proxmox_endpoint}:{proxmox_api_port}/api2/json
          insecure: true
          token_id: {proxmox_user}!{proxmox_token_name}
          token_secret: {proxmox_token_value}
          region: {cluster_name}'''

  # generate cloud controller yaml
  ccm_manifest = f'''
---
apiVersion: helm.cattle.io/v1
kind: HelmChart
metadata:
  name: proxmox-cloud-controller-manager
  namespace: kube-system
spec:
  bootstrap: true
  chart: oci://ghcr.io/sergelogvinov/charts/proxmox-cloud-controller-manager
  valuesContent: |-{controller_common}
    useDaemonSet: true
    nodeSelector:
      node-role.kubernetes.io/control-plane: "true"
'''
  kopsrox_manifest.write(ccm_manifest)

  # generate csi yaml
  csi_manifest = f'''
---
apiVersion: v1
kind: Namespace
metadata:
  labels:
    kubernetes.io/metadata.name: csi-proxmox
    pod-security.kubernetes.io/enforce: privileged
  name: csi-proxmox
---
apiVersion: helm.cattle.io/v1
kind: HelmChart
metadata:
  name: proxmox-csi-plugin
  namespace: csi-proxmox
spec:
  chart: oci://ghcr.io/sergelogvinov/charts/proxmox-csi-plugin
  valuesContent: |-{controller_common}
    storageClass:
      - name: proxmox-csi
        storage: {proxmox_storage}
        annotations:
          storageclass.kubernetes.io/is-default-class: 'true'
'''
  kopsrox_manifest.write(csi_manifest)
  kopsrox_manifest.close()

  # generate kopsrox.sh script
  k3s_script_local = open('./lib/scripts/kopsrox.sh', 'w')
  k3s_ver = f'cat /root/scripts/k3s.sh | INSTALL_K3S_VERSION={k3s_version}'
  k3s_opt = f'--kubelet-arg --cloud-provider=external --kubelet-arg --provider-id=proxmox://{cluster_name}/${2}'
  k3s_server = f'--server https://{network_ip}:6443'
  k3s_master = f'{k3s_ver} sh -s - server --cluster-init {k3s_opt}'
  k3s_slave = f'{k3s_ver} sh -s - server {k3s_server} {k3s_opt}'
  k3s_worker = f'rm -rf /etc/rancher/k3s/* && {k3s_ver} sh -s - agent {k3s_server} {k3s_opt}'
  k3s_script = f'''\
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

'''
  k3s_script_local.write(k3s_script)
  k3s_script_local.close()
  os.chmod('./lib/scripts/kopsrox.sh', 0o755)

  # generate a k3s server config file
  k3s_server_config = f'''\
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
  - local-storage
etcd-s3: true
etcd-disable-snapshot: true
etcd-snapshot-retention: 7
etcd-s3-endpoint: {s3_endpoint}
etcd-s3-access-key: {access_key}
etcd-s3-secret-key: {access_secret}
etcd-s3-bucket: {bucket}
etcd-s3-skip-ssl-verify: true
etcd-snapshot-compress: true'''

  # handle s3 region
  if region_string != '':
    k3s_server_config = k3s_server_config + f'''
etcd-s3-region: {region_string}'''

  k3s_server_file = './lib/manifests/config.yaml'
  k3s_server_yaml = open(k3s_server_file, 'w')
  k3s_server_yaml.write(k3s_server_config)
  k3s_server_yaml.close()

  # shouldn't really need root/sudo but run into permissions problems
  kmsg(f'{kname}virt-customize', f'installing {image_packages}')
  virtc_cmd = f'''\
sudo virt-customize -a {cloud_image} \
--install {image_packages} \
--mkdir /var/lib/rancher/k3s/server/manifests \
--mkdir /etc/rancher/k3s \
--upload {kopsrox_yaml}:/var/lib/rancher/k3s/server/manifests/ \
--upload {k3s_server_file}:/etc/rancher/k3s/ \
--run-command 'sed -i "s/GRUB_CMDLINE_LINUX=.*/GRUB_CMDLINE_LINUX=\"ipv6.disable=1\"/" /etc/default/grub' \
--run-command 'update-grub' \
--copy-in ./lib/scripts:/root \
> virt-customize.log 2>&1'''
  local_exec(virtc_cmd)

  # destroy template if it exists
  try:
    prox_destroy(cluster_id)
  except:
    pass

  # define image desc
  img_ts = str(datetime.now())
  image_desc = f'''
cluster_name: {cluster_name}
cloud_img: {cloud_image}
k3s_version: {k3s_version}
created: {img_ts}'''

  # create new server
  prox_task(prox.nodes(proxmox_node).qemu.post(
    vmid = cluster_id,
    cores = 1,
    memory = 1024,
    bios = 'ovmf',
    efidisk0 = f'{proxmox_storage}:0',
    machine = 'q35',
    cpu = ('cputype=x86-64-v3'),
    scsihw = 'virtio-scsi-single',
    name = f'{cluster_name}-i0',
    ostype = 'l26',
    scsi2 = (f'{proxmox_storage}:cloudinit'),
    tags = cluster_name,
    serial0 = 'socket',
    agent = ('enabled=true'),
    hotplug = 0,
    ciupgrade = 0,
    description = image_desc,
    ciuser = cloudinituser,
    cipassword = cloudinitpass,
    sshkeys = cloudinitsshkey,
  ))

  # shell to import disk
  # import-from requires the full path os.getcwd required here
  import_cmd = f'''
sudo qm set {cluster_id} --scsi0 {proxmox_storage}:0,import-from={os.getcwd()}/{cloud_image},iothread=true,aio=native
mv {cloud_image} {cloud_image}.patched'''

  # run shell command to import
  kmsg(f'{kname}qm-import', f'importing disk')
  local_exec(import_cmd)

  # convert to template via create base disk also vm config
  prox_task(prox.nodes(proxmox_node).qemu(cluster_id).template.post())
  prox_task(prox.nodes(proxmox_node).qemu(cluster_id).config.post(template = 1))

# image info
if cmd == 'info':
  image_info()

# destroy image
if cmd == 'destroy':
   kmsg(f'{kname}destroy', f'{kopsrox_img()}/{cloud_image_desc}', 'sys')
   prox_destroy(cluster_id)
