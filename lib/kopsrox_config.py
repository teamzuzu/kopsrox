#!/usr/bin/env python3

# external imports
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import requests
from datetime import datetime
from proxmoxer import ProxmoxAPI
import re,os,sys,subprocess,time,base64

# kmsg
from kopsrox_kmsg import kmsg, kabort, kstep, kplan, kplan_tick

# read ini file into config
from configparser import ConfigParser
kopsrox_config = ConfigParser()
kopsrox_config.read('kopsrox.ini')
config = ({s:dict(kopsrox_config.items(s)) for s in kopsrox_config.sections()})

# kname
kname='config_check'
passed_cmd = sys.argv[1]

# check section and value exists in kopsrox.ini
def conf_check(value: str = 'kopsrox'):

  # check option exists
  if not kopsrox_config.has_option('kopsrox', value):
    kabort(kname, f'{value} is missing in kopsrox.ini')

  # define config_item
  config_item = kopsrox_config.get('kopsrox', value)

  # check value is not blank - some values may be
  if config_item == '' and value not in ['extra_packages', 's3_region']:
    kabort(kname, f'{value} - a value is required')

  # int check
  if value in ['proxmox_api_port', 'vm_cpu', 'vm_ram', 'vm_disk', 'cluster_id', 'workers', 'masters', 'network_mtu']:
    try:
      return int(config_item)
    except:
      kabort(kname, f'{value} should be numeric: {config_item}')

  # return string
  return str(config_item)

# cluster name
cluster_name = conf_check('cluster_name')
kname = f'{cluster_name}_config-check'

# cluster id
cluster_id = conf_check('cluster_id')
if cluster_id < 100:
  kabort(kname, f'cluster_id is too low - should be over 100')

# assign master id
masterid = int(cluster_id) + 1

# proxmox endpoint
proxmox_endpoint = conf_check('proxmox_endpoint')
if ( proxmox_endpoint == "localhost" or proxmox_endpoint == "127.0.0.1" ):
  kabort(kname, f'proxmox_endpoint cannot be localhost - please use a reachable IP')

# proxmox vars
proxmox_user = conf_check('proxmox_user')
proxmox_token_name = conf_check('proxmox_token_name')
proxmox_api_port = conf_check('proxmox_api_port')
proxmox_token_value = conf_check('proxmox_token_value')

# test connection to proxmox
try:
  prox = ProxmoxAPI(
    proxmox_endpoint,
    port=proxmox_api_port,
    user=proxmox_user,
    token_name=proxmox_token_name,
    token_value=proxmox_token_value,
    verify_ssl=False,
    timeout=5)

  # check connection to cluster
  prox.cluster.status.get()

except Exception as e:
  kabort(kname, f'API connection to proxmox failed check proxmox settings\n{e}')

# map node name
proxmox_node = conf_check('proxmox_node')
disc_nodes = [node.get('node', None) for node in prox.nodes.get()]
if proxmox_node not in disc_nodes:
  kabort(kname, f'"{proxmox_node}" not found - discovered nodes: {disc_nodes}')

# try k8s ping
conf_check_master_up = False
try:
  k3s_ping = prox.nodes(proxmox_node).qemu(masterid).agent.exec.post(command = '/usr/local/bin/k3s kubectl version')
  conf_check_master_up = True

except:
  try:
    qa_ping = prox.nodes(proxmox_node).qemu(masterid).agent.ping.post()
    kmsg(kname, f'k3s down but master server available...?', 'sys')
  except:
    pass

# storage
proxmox_storage = conf_check('proxmox_storage')
if not prox.nodes(proxmox_node).storage.get(storage = proxmox_storage):
  kabort(kname, f'{proxmox_storage} storage not found')

# image related config checks
if passed_cmd == 'image':

  # oci image used to build the microvm template
  oci_image = conf_check('oci_image')

  # pve-microvm version checks
  # kopsrox needs 0.3.19+ ( qm shutdown fix and the template layout we patch )
  microvm_ver = subprocess.run(['bash', '-c', "dpkg-query -W -f '${Version}' pve-microvm 2>/dev/null || echo none"], text=True, capture_output=True).stdout.strip()
  if microvm_ver == 'none':
    kabort(kname, 'pve-microvm is not installed - see docs/SETUP.md')
  microvm_installed = tuple(map(int, microvm_ver.split('-')[0].split('.')))
  if microvm_installed < (0, 3, 19):
    kabort(kname, f'pve-microvm {microvm_ver} is too old - kopsrox needs 0.3.19 or later')

  # notify if upstream has a newer release - skip quietly if offline
  try:
    microvm_latest_tag = requests.get('https://api.github.com/repos/rcarmo/pve-microvm/releases/latest', timeout=3).json()['tag_name']
    if tuple(map(int, microvm_latest_tag.lstrip('v').split('.'))) > microvm_installed:
      kmsg(kname, f'pve-microvm {microvm_latest_tag} is available ( installed: {microvm_ver} ) - restart pvedaemon after upgrading!', 'sys')
  except:
    pass

  # kopsrox microvm kernel/initrd - optional overrides
  microvm_kernel = kopsrox_config.get('kopsrox', 'microvm_kernel', fallback='/usr/share/pve-microvm/vmlinuz-kopsrox')
  microvm_initrd = kopsrox_config.get('kopsrox', 'microvm_initrd', fallback='/usr/share/pve-microvm/initrd-kopsrox')

  # template may not exist yet on image create
  try:
    template_data = prox.nodes(proxmox_node).qemu(cluster_id).config.get()
    cloud_image_desc = template_data['description']
  except:
    cloud_image_desc = ''

# vm disk
vm_disk = conf_check('vm_disk')
if vm_disk < 20:
  kabort(kname, f'vm_ - kopsrox vms need 20G disk')

# vm cpu
vm_cpu = conf_check('vm_cpu')
if vm_disk < 1:
  kabort(kname, f'vm_ - kopsrox vms at least 1 cpu')

# ram size check
vm_ram = conf_check('vm_ram')
if vm_ram < 2:
  kabort(kname, f'vm_ram - kopsrox vms need 2G RAM')

# cloudinit
cloudinituser = conf_check('cloudinituser')
cloudinitpass = conf_check('cloudinitpass')
cloudinitsshkey = conf_check('cloudinitsshkey')
if not cloudinitsshkey.startswith('ssh-'):
  kabort(kname, f'[kopsrox]/cloudinitsshkey - invalid ssh key')

# extra packages installed into each node at init
extra_packages = conf_check('extra_packages')

# network
network_ip = conf_check('network_ip')
network_gw = conf_check('network_gw')
network_mask = conf_check('network_mask')
network_dns = conf_check('network_dns')
network_bridge = conf_check('network_bridge')
network_mtu = conf_check('network_mtu')

# variables for network and its IP for vmip function
network_octs = network_ip.split('.')
network_base = f'{network_octs[0]}.{network_octs[1]}.{network_octs[2]}.'
network_ip_prefix = int(network_octs[-1])

# master + check
masters = conf_check('masters')
if not (masters == 1 or masters == 3):
  kabort(kname, f'[cluster] - masters: only 1 or 3 masters supported. You have: {masters}')

# workers
workers = conf_check('workers')

# k3s version
k3s_version = conf_check('k3s_version')

# s3 stuff
region = conf_check('s3_region')
s3_endpoint = conf_check('s3_endpoint')
access_key = conf_check('s3_access-key')
access_secret = conf_check('s3_access-secret')
bucket = conf_check('s3_bucket')

# region optional
region_string = ''
if region:
  region_string = region

# define vmnames
suffixes = ['-i0', '-m1', '-m2', '-m3', '-u1', '-w1', '-w2', '-w3', '-w4', '-w5']
vmnames = {
  cluster_id + i: f"{cluster_name}{suffix}"
  for i, suffix in enumerate(suffixes)
}

# look up kopsrox_img name
def kopsrox_img():

  # list contents
  for image in prox.nodes(proxmox_node).storage(proxmox_storage).content.get():

    # map image_name
    image_name = image.get("volid")

    # if 123-disk-0 found in volid
    if re.search(f'{cluster_id}-disk-0', image_name):
      return(image_name)

  # unable to find image name
  return False

# return dict of kopsrox vms by node
def list_kopsrox_vm():

  # init dict
  vmids = {}

  # get all vms running on proxmox
  for vm in prox.cluster.resources.get(type = 'vm'):

    # map id
    vmid = int(vm.get('vmid'))

    # if vmid is in kopsrox config range ie between cluster_id and cluster_id + 10
    # add vmid and node to dict
    if (vmid >= cluster_id) and (vmid < (cluster_id + 10)):
      vmids[vmid] = vm.get('node')

  # return sorted dict
  return(dict(sorted(vmids.items())))


# check configured bridge exists or is a sdn vnet
# configured bridge does not contain the string 'sdn/'
if passed_cmd == 'image' and not conf_check_master_up:
  if not re.search('sdn/', network_bridge):
    discovered_bridges = [bridge.get('iface', None) for bridge in prox.nodes(proxmox_node).network.get(type = 'bridge')]
  else:
    # check we can map zone and get vnets
    try:
      sdn_params = network_bridge.split('/')
      zone = sdn_params[1]
      network_bridge = sdn_params[2]
    except:
      kabort(kname, f'unable to parse sdn config: "{network_bridge}"')

    # discover available sdn bridges
    discovered_bridges = [bridge.get('vnet', None) for bridge in prox.nodes(proxmox_node).sdn.zones(zone).content.get()]

  # check configured bridge is in list
  if network_bridge not in discovered_bridges:
    kabort(kname, f'"{network_bridge}" not found. valid bridges: {discovered_bridges}')

# check the image exists - image create builds it so skips the check
if not (sys.argv[1] == 'image' and sys.argv[2:3] == ['create']):
  try:
    img_found = kopsrox_img()
  except:
    img_found = False
  if not img_found:
    kabort(kname, f'{cluster_name} image not found - please run "kopsrox image create"')

# vm not powered on check
# vms var used in other code now and needs renaming
vms = list_kopsrox_vm()
for vmid in vms:

  # skip image
  if vmid != cluster_id:

    # get vminfo
    vmi = prox.nodes(proxmox_node).qemu(vmid).status.current.get()

    # start stopped nodes
    if vmi['status'] == 'stopped':
      kmsg(kname, f'powering on {vmi["name"]}', 'sys')
      prox.nodes(proxmox_node).qemu(vmid).status.start.post()

# get token if it exists
def get_k3s_token():
  token_fname = f'{cluster_name}.k3stoken'
  if os.path.isfile(token_fname):
    return(open(token_fname, "r").read())

# return ip for vmid
def vmip(vmid: int):
  # last number of network + ( vmid - cluster_id )
  # eg 160 + ( 601 - 600 )  = 161
  ip = f'{network_base}{(network_ip_prefix + (vmid - cluster_id))}'
  return(ip)

# run local os process
def local_exec(cmd):
  cmd_run = subprocess.run(['bash', "-c", cmd], text=True, capture_output=True)

  # if return code 1 or any stderr
  if (cmd_run.returncode == 1 or cmd_run.stderr != ''):
    kabort('local_exec-process-error', f'{cmd}\n{cmd_run.stderr.strip()}')
  return(cmd_run)

# print image info
def image_info():
  kname = f'image_'
  kmsg(f'{kname}desc', cloud_image_desc)
  kmsg(f'{kname}storage', f'{kopsrox_img()}')
