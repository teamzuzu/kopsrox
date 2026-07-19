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

# read and validate kopsrox.ini against the schema - injects every option
# as a module global ( see SCHEMA in kopsrox_schema.py for the names )
from configparser import ConfigParser
from kopsrox_schema import validate
kopsrox_config = ConfigParser()
kopsrox_config.read('kopsrox.ini')
globals().update(validate(kopsrox_config))

# kname and the verb being run
kname = f'{cluster_name}_config-check'
passed_cmd = sys.argv[1]

# assign master id
masterid = int(cluster_id) + 1

# variables for network and its IP for vmip function
network_octs = network_ip.split('.')
network_base = f'{network_octs[0]}.{network_octs[1]}.{network_octs[2]}.'
network_ip_prefix = int(network_octs[-1])

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

# discover cluster state - one call covers nodes, storage, vms and the image
try:
  resources = prox.cluster.resources.get()
except Exception as e:
  kabort(kname, f'unable to list cluster resources\n{e}')

# map node name
disc_nodes = [r.get('node') for r in resources if r.get('type') == 'node']
if proxmox_node not in disc_nodes:
  kabort(kname, f'"{proxmox_node}" not found - discovered nodes: {disc_nodes}')

# storage
if not [r for r in resources if r.get('type') == 'storage' and r.get('node') == proxmox_node and r.get('storage') == proxmox_storage]:
  kabort(kname, f'{proxmox_storage} storage not found')

# kopsrox vms in the cluster id range - full resource entries kept for status/name
disc_vms = {int(r['vmid']): r for r in resources
            if r.get('type') == 'qemu' and cluster_id <= int(r['vmid']) < cluster_id + 10}

# map of kopsrox vmid -> proxmox node
vms = {vmid: disc_vms[vmid].get('node') for vmid in sorted(disc_vms)}

# check the image exists - image create builds it so skips the check
if not (passed_cmd == 'image' and sys.argv[2:3] == ['create']):
  if cluster_id not in vms:
    kabort(kname, f'{cluster_name} image not found - please run "kopsrox image create"')

# guest verbs power on any stopped node
if passed_cmd in ['cluster', 'k3s', 'etcd', 'node']:
  for vmid in vms:
    if vmid != cluster_id and disc_vms[vmid].get('status') == 'stopped':
      kmsg(kname, f'powering on {disc_vms[vmid].get("name")}', 'sys')
      prox.nodes(vms[vmid]).qemu(vmid).status.start.post()

# master reachable? agent ping only - consumed by the image bridge gate and cluster plan totals
# an agent-alive proxy for k3s-alive: cluster_plan_total can over-count one export unit, the bar clamps
conf_check_master_up = False
if passed_cmd in ['image', 'cluster'] and disc_vms.get(masterid, {}).get('status') == 'running':
  try:
    prox.nodes(vms[masterid]).qemu(masterid).agent.ping.post()
    conf_check_master_up = True
  except:
    pass

# image related config checks
if passed_cmd == 'image':

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

  # template may not exist yet on image create
  try:
    template_data = prox.nodes(proxmox_node).qemu(cluster_id).config.get()
    cloud_image_desc = template_data['description']
  except:
    cloud_image_desc = ''

  # check configured bridge exists or is a sdn vnet - skipped when the cluster is already live
  if not conf_check_master_up:
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
