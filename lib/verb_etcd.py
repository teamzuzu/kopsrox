#!/usr/bin/env python3

# kopsrox
from kopsrox_k3s import *

# passed command
cmd = sys.argv[2]
kname = f'etcd_{cmd}'

# token filename
token_fname = f'{cluster_name}.k3stoken'

# check master is running / exists
try:
  node = vms[masterid]
except:
  kabort(f'{kname}-check', 'cluster does not exist')

# check token
try:
  get_k3s_token()
except:
  kabort(f'{kname}-check', 'problem with k3s token')

# run k3s s3 command passed
def s3_run(s3cmd):

  # run the command ( 2>&1 required )
  k3s_run = f'k3s etcd-snapshot {s3cmd} 2>&1'
  s3_out = qa_exec(masterid,k3s_run)

  # look for fatal error in output
  if re.search('level=fatal', s3_out):
    kabort(f'{kname}-s3run', f'\n {s3_out}')

  # return command outpit
  return(s3_out)

# list images in s3 storage
def list_snapshots():

  # run s3 ls and create a list per line
  ls = s3_run('ls').split('\n')

  # images string
  images = ''

  # for each image in the sorted list
  for line in sorted(ls):

    # map filename
    s3_file = line.split()[0]

    # if cluster name matches the s3 line append to the images string
    if re.search(f'kopsrox-{cluster_name}', s3_file) and re.search('s3', line):
      images += f'{s3_file} - {line.split()[3]}\n'

  # return images string
  return(images.strip())

# test connection to s3 by getting list of snapshots
try:
  snapshots = list_snapshots()
except:
  kabort(f'{kname}-check', 'error getting data from s3 repo')

# s3 prune
if cmd == 'prune':
  kmsg(f'{kname}-prune', (f'{s3_endpoint}/{bucket}\n' + s3_run('prune --name kopsrox')), 'sys')
  exit(0)

# print s3List
def s3_list():
   kmsg('etcd_repo', f'{s3_endpoint}/{bucket}\n{snapshots}')

# snapshot
if cmd == 'snapshot':

  # run save
  snapout = s3_run('save --name kopsrox').split('\n')
  last_line = ''
  for snap_out in snapout:
    if not re.search(' level=warning msg="Unknown flag', snap_out):
      if (last_line != snap_out):
        kmsg(kname, snap_out, 'sys')
        last_line = snap_out

  # list snapshots
  snapshots = list_snapshots()
  s3_list()

# list
if cmd == 'list':
  s3_list()
  exit(0)

# restore / list snapshots
if cmd == 'restore' or cmd == 'restore-latest':

  # restore snapshot
  snapshot = sys.argv[3]

  # check passed snapshot name exists
  if not re.search(snapshot,snapshots):
    kmsg(kname, f'{snapshot} not found', 'err')
    s3_list()
    exit(1)

  # info
  kmsg(kname,f'restoring {snapshot}', 'sys')
  k3s_rm_cluster()
  clone(masterid)
  k3s_init_node(masterid, 'restore', snapshot)

  # delete extra nodes in the restored cluster
  nodes = kubectl('get nodes').split()

  # for each of returned nodes from kubectl
  for node in nodes:

    # if matches cluster name and not master node
    if re.search(f'{cluster_name}-', node) and (node != f'{cluster_name}-m1'):
      kmsg(kname, f'removing stale node {node}', 'sys')

      # need to check this..
      kubectl(f'delete node {node}')

  # get restored clusters kubeconfig and token
  kubeconfig()
  export_k3s_token()

  # run k3s update
  k3s_update_cluster()
