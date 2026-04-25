#!/usr/bin/env python3

# external imports
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import urllib.parse
import requests
from datetime import datetime
from proxmoxer import ProxmoxAPI
import re,os,sys,subprocess,time,wget,base64

# kmsg
from kopsrox_kmsg import kmsg

# read ini file into config
from configparser import ConfigParser
kopsrox_config = ConfigParser()
kopsrox_config.read('kopsrox.ini')
config = ({s:dict(kopsrox_config.items(s)) for s in kopsrox_config.sections()})

# kname
kname='config_check'
passed_cmd = sys.argv[1]

# check section and value exists in kopsrox.ini
def conf_check(value: str = 'kopsrox'):

  # check option exists
  try:
    if not kopsrox_config.has_option('kopsrox',value):
      exit(0)
  except:
    kmsg(kname, f'{value} is missing in kopsrox.ini','err')
    exit(0)

  # check value is not blank
  try:

    # values that can be blank
    if (kopsrox_config.get('kopsrox', value) == '') and  \
    (value not in ['extra_packages', 's3_region']):
      exit(0)

  except:
    kmsg(kname, f'{value} - a value is required','err')
    exit(0)

  # define config_item
  config_item = kopsrox_config.get('kopsrox', value)

  # int check
  if value in ['proxmox_api_port', 'vm_cpu', 'vm_ram', 'vm_disk', 'cluster_id', 'workers', 'masters', 'network_mtu']:

    # test if var is int
    try:
      test_var = int(config_item)
    except:
      kmsg(kname, f'[{value} should be numeric: {config_item}', 'err')
      exit(0)

    # return int
    return(kopsrox_config.getint('kopsrox', value))
  else:
    # return string
    return(str(config_item))

# cluster name
cluster_name = conf_check('cluster_name')
kname = f'{cluster_name}_config-check'

# cluster id
cluster_id = conf_check('cluster_id')
if cluster_id < 100:
  kmsg(kname, f'cluster_id is too low - should be over 100', 'err')
  exit(0)

# assign master id
masterid = int(cluster_id) + 1

# proxmox endpoint
proxmox_endpoint = conf_check('proxmox_endpoint')
if ( proxmox_endpoint == "localhost" or proxmox_endpoint == "127.0.0.1" ):
  kmsg(kname, f'proxmox_endpoint cannot be localhost - please use a reachable IP', 'err')
  exit(0)

# proxmox vars
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

except:
  kmsg(kname, f'API connection to proxmox failed check proxmox settings', 'err')
  print(prox.cluster.status.get())
  exit(0)

# map node name
proxmox_node = conf_check('proxmox_node')
disc_nodes = [node.get('node', None) for node in prox.nodes.get()]
if proxmox_node not in disc_nodes:
  kmsg(kname, f'"{proxmox_node}" not found - discovered nodes: {disc_nodes}', 'err')
  exit(0)

# try k8s ping
conf_check_master_up = False
try:
  k3s_ping = prox.nodes(proxmox_node).qemu(masterid).agent.exec.post(command = '/usr/local/bin/k3s kubectl version')
  conf_check_master_up = True

except:
  try:
    qa_ping = prox.nodes(proxmox_node).qemu(masterid).agent.ping.post()
    kmsg(kname, f'k3s down but master server available...?', 'err')
  except:
    pass

# storage
proxmox_storage = conf_check('proxmox_storage')
if not prox.nodes(proxmox_node).storage.get(storage = proxmox_storage):
  kmsg(kname, f'{proxmox_storage} storage not found', 'err')
  print(disc_storages)

# image related config checks
# cloud image
if passed_cmd == 'image':
  cloud_image_url = conf_check('cloud_image_url')

  # deal with any extra packages aside from qemu-guest-agent
  image_packages = 'qemu-guest-agent'
  extra_packages = f',{conf_check('extra_packages')}'
  if extra_packages != ',':
    image_packages = f'{image_packages}{extra_packages}'

  # check ssh key can be encoded correctly
  try:
    cloudinitsshkey = urllib.parse.quote(conf_check('cloudinitsshkey'), safe='')
  except:
    kmsg(kname, f'[kopsrox]/cloudinitsshkey - invalid ssh key', 'err')
    exit(0)

  template_data = prox.nodes(proxmox_node).qemu(cluster_id).config.get()
  cloud_image_desc = template_data['description']

# vm disk
vm_disk = conf_check('vm_disk')
if vm_disk < 20:
  kmsg(kname, f'vm_ - kopsrox vms need 20G disk', 'err')
  exit(0)

# vm cpu
vm_cpu = conf_check('vm_cpu')
if vm_disk < 1:
  kmsg(kname, f'vm_ - kopsrox vms at least 1 cpu', 'err')
  exit(0)

# ram size check
vm_ram = conf_check('vm_ram')
if vm_ram < 2:
  kmsg(kname, f'vm_ram - kopsrox vms need 2G RAM', 'err')
  exit(0)

# cloudinit
cloudinituser = conf_check('cloudinituser')
cloudinitpass = conf_check('cloudinitpass')

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

# master + check
masters = conf_check('masters')
if not (masters == 1 or masters == 3):
  kmsg(kname, f'[cluster] - masters: only 1 or 3 masters supported. You have: {masters}')
  exit(0)

# workers
workers = conf_check('workers')

# k3s version
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

# look up kopsrox_img name
def kopsrox_img():

  # list contents
  for image in prox.nodes(proxmox_node).storage(proxmox_storage).content.get():

    # map image_name
    image_name = image.get("volid")

    # if 123-disk-0 found in volid
    if re.search(f'{cluster_id}-disk-0', image_name):
      return(image_name)

  # unable to find image name
  return False

# return dict of kopsrox vms by node
def list_kopsrox_vm():

  # init dict
  vmids = {}

  # get all vms running on proxmox
  for vm in prox.cluster.resources.get(type = 'vm'):

    # map id
    vmid = int(vm.get('vmid'))

    # if vmid is in kopsrox config range ie between cluster_id and cluster_id + 10
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
      if not sdn_params[1] or not sdn_params[2]:
        exit(0)
      else:
        zone = sdn_params[1]
        network_bridge = sdn_params[2]
    except:
      kmsg(kname, f'unable to parse sdn config: "{network_bridge}"', 'err')
      exit(0)

    # discover available sdn bridges
    discovered_bridges = [bridge.get('vnet', None) for bridge in prox.nodes(proxmox_node).sdn.zones(zone).content.get()]

  # check configured bridge is in list
  if network_bridge not in discovered_bridges:
    kmsg(kname, f'"{network_bridge}" not found. valid bridges: {discovered_bridges}', 'err')
    exit(0)

# skip image check if image create is passed
try:
  # check for image create command line
  if sys.argv[1] == 'image' and sys.argv[2] == 'create':
    pass
  else:
    exit(0)

# image checks
except:

  # check image exists
  try:
    # exit if image does not exist
    if not kopsrox_img():
      exit(0)
  except:
    # no image found
    kmsg(kname, f'{cluster_name} image not found - please run "kopsrox image create"', 'err')
    exit(0)

# vm not powered on check
# vms var used in other code now and needs renaming
vms = list_kopsrox_vm()
for vmid in vms:

  # skip image
  if vmid != cluster_id:

    # get vminfo
    vmi = prox.nodes(proxmox_node).qemu(vmid).status.current.get()

    # start stopped nodes
    if vmi['status'] == 'stopped':
      kmsg(kname, f'powering on {vmi["name"]}', 'sys')
      prox.nodes(proxmox_node).qemu(vmid).status.start.post()

# get token if it exists
def get_k3s_token():
  token_fname = f'{cluster_name}.k3stoken'
  if os.path.isfile(token_fname):
    return(open(token_fname, "r").read())
    exit(0)

# return ip for vmid
def vmip(vmid: int):
  # last number of network + ( vmid - cluster_id )
  # eg 160 + ( 601 - 600 )  = 161
  ip = f'{network_base}{(network_ip_prefix + (vmid - cluster_id))}'
  return(ip)

# run local os process
def local_exec(cmd):
  try:
    cmd_run = subprocess.run(['bash', "-c", cmd], text=True, capture_output=True)

    # if return code 1 or any stderr
    if (cmd_run.returncode == 1 or cmd_run.stderr != ''):
      exit(0)
  except:
    kmsg('local_exec-process-error', f'{cmd_run} - {cmd_run.stderr.strip()}', 'err')
    exit(0)
  return(cmd_run)

# print image info
def image_info():
  kname = f'image_'
  kmsg(f'{kname}desc', cloud_image_desc)
  kmsg(f'{kname}storage', f'{kopsrox_img()}')
