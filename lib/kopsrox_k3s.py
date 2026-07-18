#!/usr/bin/env python3

# imports
from kopsrox_proxmox import *

# check for k3s status
def k3s_check(vmid: int):

  # test call
  try:
    get_node = kubectl('get node ' + vmnames[vmid])
  except:
    return False

  # return true if Ready
  # word boundary as NotReady also contains Ready
  if re.search(r'\bReady\b', get_node):
    return True
  return False

# create a master/slave/worker
def k3s_init_node(vmid: int = masterid,nodetype = 'master',snapshot = 'kopsrox'):

  # nodetype error check
  if nodetype not in ['master', 'slave', 'worker', 'restore']:
    kmsg('k3s_init-node', f'{nodetype} invalid nodetype', 'err')
    exit(0)

  # check node has internet
  try:
    internet_check(vmid)
  except:
    kmsg('k3s_init-node', f'{vmid} no internet', 'err')
    exit(0)

  # if k3s is up  on existing master node
  if k3s_check(vmid):
     return

  # master
  if nodetype in ['master', 'worker', 'slave']:
    kmsg(f'k3s_{nodetype}-init', f'configuring {k3s_version} on {vmnames[vmid]}')
    init_cmd = f'/root/scripts/kopsrox.sh {nodetype} {vmid} {get_k3s_token()}'

  # restore
  if nodetype == 'restore':
    if snapshot == 'kopsrox':
      bs_cmd = f'/root/scripts/kopsrox.sh latest {masterid} {get_k3s_token()}'
      bs_cmd_out = qa_exec(masterid,bs_cmd)

      # sort ls output so last is latest snapshot
      for snap in sorted(bs_cmd_out.split('\n')):
        if re.search(f'kopsrox-{cluster_name}', snap.split()[0]):
            latest = snap.split()[0]
      snapshot = latest

    kmsg(f'k3s_restore', f'restoring {snapshot}')
    init_cmd = f'/root/scripts/kopsrox.sh restore {snapshot} {get_k3s_token()}'

  # write log of install on node
  init_cmd = init_cmd + f' > /k3s_{nodetype}_install.log 2>&1'

  # run command
  qa_exec(vmid,init_cmd)

  # wait until ready
  wait: int = 20
  count: int = 1
  status = ''
  while not status == 'Ready':
    try:
      if not k3s_check(vmid):
        exit(0)
      status = 'Ready'
    except:
      count += 1

    if count == wait:
       kmsg('k3s_check', f'timed out after {wait}s for {vmnames[vmid]}', 'err')
       exit(0)
    time.sleep(1)

  # final steps for first master / restore export kubeconfig and token
  if nodetype in ['master', 'restore']:
    kubeconfig()
    export_k3s_token()

# remove a node
def k3s_remove_node(vmid: int):

  # get vmname
  vmname = vmnames[vmid]
  kmsg('k3s_remove-node', vmname)

  if vmname != f'{cluster_name}-m1':
    kubectl('cordon ' + vmname)
    kubectl('drain --timeout=10s --delete-emptydir-data --ignore-daemonsets --force ' + vmname)
    kubectl('delete node ' + vmname)
    # remove the node password secret or a rebuilt node with this name gets rejected
    kubectl(f'-n kube-system delete secret {vmname}.node-password.k3s --ignore-not-found')

  # destroy vm
  prox_destroy(vmid)

# remove cluster - leave master if restore = true
def k3s_rm_cluster():

  # list all kopsrox vm id's
  for vmid in sorted(list_kopsrox_vm(), reverse = True):

    # map hostname
    vmname = vmnames[vmid]

    # do not delete image or utility node
    if vmname == f'{cluster_name}-i0' or vmname == f'{cluster_name}-u1':
      continue

    # remove node from cluster and proxmox
    if vmname == f'{cluster_name}-m1':
      prox_destroy(vmid)
    else:
      k3s_remove_node(vmid)

# builds or removes other nodes from the cluster as required per config
def k3s_update_cluster():
 kmsg('k3s_update-cluster', f'{cluster_name}/{k3s_version} - checking {masters} masters and {workers} workers', 'sys')

 # checks the master node
 k3s_init_node()

 # get list of running vms
 vmids = list_kopsrox_vm()

 # do we need to run any more masters
 if masters > 1:
  master_count = int(1)

  while ( master_count <=  2 ):

    # so eg 601 + 1 = 602 = m2
    slave_masterid = masterid + master_count
    slave_hostname = vmnames[slave_masterid]

    # existing server
    if slave_masterid not in vmids:
      clone(slave_masterid)

    # install k3s on slave and join master
    k3s_init_node(slave_masterid,'slave')

    # next possible master ( m3 )
    master_count = master_count + 1

 # check for extra masters
 if masters == 1:
   for vm in vmids:
     # is this required?
     vm = int(vm)

     # if vm is in the range of masterids
     if vm == (masterid + 1 ) or vm == (masterid + 2 ):
       # remove the vm
       k3s_remove_node(vm)

 # define default workerid ( -1 )
 workerid = masterid + 3

 # create new worker nodes per config
 if workers > 0:

   # first id in the loop
   worker_count: int = 1

   # cycle through possible workers
   while ( worker_count <= workers ):
     # calculate workerid
     workerid = masterid + 3 + worker_count

     # if existing vm with this id found
     if workerid not in vmids:
       clone(workerid)

     # checks worker has k3s installed first
     k3s_init_node(workerid,'worker')
     worker_count = worker_count + 1

 # remove extra workers
 for vm in vmids:
   if vm > workerid:
     kmsg('k3s_extra-worker', vmnames[vm])
     k3s_remove_node(vm)

 # display cluster info
 cluster_info()

# kubeconfig
def kubeconfig():

  # define filename
  kubeconfig = f'{cluster_name}.kubeconfig'
  # replace 127.0.0.1 with vip ip
  kconfig = qa_exec(masterid, 'cat /etc/rancher/k3s/k3s.yaml').replace('127.0.0.1', network_ip)
  with open(kubeconfig, 'w') as new_kubeconfig:
    new_kubeconfig.write(kconfig)
  kmsg('k3s_kubeconfig', f'saved {kubeconfig}')

# kubectl
def kubectl(cmd):
  k3s_cmd = f'/usr/local/bin/kubectl {cmd} 2>&1'
  kcmd = qa_exec(masterid,k3s_cmd)
  return(kcmd)

# run k3s check config
def k3s_check_config():
  kmsg('k3s_check-config', 'checking k3s config')
  k3s_cmd = f'/usr/local/bin/k3s check-config'
  kcmd = qa_exec(masterid,k3s_cmd)
  print(kcmd)

# export k3s token
def export_k3s_token():

  # define token file name
  token_name = f'{cluster_name}.k3stoken'
  # get masters token
  live_token = qa_exec(masterid, 'cat /var/lib/rancher/k3s/server/token')

  # check existing token
  if os.path.isfile(token_name):

    saved_token = open(token_name, "r").read()
    # difference between live and local token
    if not saved_token == live_token:

      # passwords are different..
      if not saved_token.split(':')[3]  == live_token.split(':')[3]:
        kmsg('k3s_export-token', 'passwords different between live system and local token! exiting', 'err')
        exit(0)

      # CA is different - expected on a new cluster
      kmsg('k3s_export-token', f'found: {token_name} updating CA')
      with open(token_name, 'w') as token_file:
        token_file.write(live_token)

    # existing token file matches live
    else:
      kmsg('k3s_export-token', f'found: {token_name} OK')

  # no token found so write new one
  else:
    with open(token_name, 'w') as token_file:
      token_file.write(live_token)
    kmsg('k3s_export-token', f'created: {token_name}')

# cluster info
def cluster_info():

  # live nodes in cluster
  cluster_info_vms = list_kopsrox_vm()

  # check m1 id exists
  if not masterid in cluster_info_vms:
    kmsg(kname, f'cluster {cluster_name} does not exist', 'err')
    exit(0)

  kmsg(f'cluster_info', '', 'sys')
  curr_master = get_kube_vip_master()

  # for kopsrox vms
  for vmid in cluster_info_vms:
    if not cluster_id == vmid:
      hostname = vmnames[vmid]
      vmstatus = f'[{cluster_info_vms[vmid]}] {vmip(vmid)}/{network_mask}'
      if hostname == curr_master:
        vmstatus += f' vip {network_ip}/{network_mask}'
      kmsg(f'{hostname}_{vmid}', f'{vmstatus}')

  # fix this
  kmsg('kubectl_get-nodes', f'\n{kubectl("get nodes")}')

# reload kubevip
def reload_kubevip():
    return(kubectl('-n kube-system rollout restart daemonset kubevip'))

# return current vip master
def get_kube_vip_master():
  kubevip_q = f'get nodes --selector kube-vip.io/has-ip={network_ip}'
  kubevip_o = kubectl(kubevip_q)
  try:
    kubevip_m = kubevip_o.split()[5]
  except:
    kubevip_m = ''
  return(kubevip_m)
