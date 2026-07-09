#!/usr/bin/env python3

# functions
from kopsrox_config import *
from kopsrox_k3s import k3s_remove_node, k3s_init_node, cluster_info
from kopsrox_proxmox import clone, node_reboot_wait

# passed command
cmd = sys.argv[2]

# map arg if passed
try:
  arg = sys.argv[3]
except:
  pass

# define kname
kname = 'node_'+cmd

# cmd runs through all vms
if cmd == 'cluster-exec':
  for vmid in vms:
    if vmid != cluster_id:
      kmsg('node_cluster-exec', f'{vmnames[vmid]} {arg}')
      os.system(f'sudo qm guest exec {vmid} {arg}')
  exit(1)


# all commands aside from utility require a hostname passed - so check them here
if cmd not in ['utility']:

  # for each vmid in list of vms generated in kopsrox_config
  for vmid in vms:

    # if passed arg matches vmname
    if arg == vmnames[vmid]:
      kmsg(kname, arg)

      # terminal 
      if cmd == 'terminal':
        kmsg('node_terminal', f'root autologin on console - or u/p: {cloudinituser} / {cloudinitpass}', 'sys')
        os.system(f'sudo qm terminal {vmid}')
        exit(0)

      # ssh command
      if cmd == 'ssh':
        os.system(f'ssh -l {cloudinituser} {vmip(vmid)} -o StrictHostKeyChecking=no ')
        exit(0)

      # destroy vm
      if cmd == 'destroy':
        k3s_remove_node(vmid)
        exit(0)

      # reboot in-guest via the agent - qm reboot hangs on microvm as
      # pve-microvm omits the qmeventd socket that reaps the halted process
      if cmd == 'reboot':
        node_reboot_wait(vmid)
        exit(0)

      # k3s uninstall
      if cmd == 'k3s-uninstall':
        os.system(f'sudo qm guest exec {vmid} /usr/local/bin/k3s-uninstall.sh')
        exit(0)

      # rejoin slave
      if cmd == 'rejoin-slave':
        k3s_init_node(vmid, 'slave')
        exit(0)

  # vm not found 
  kmsg(kname, f'{arg} vm not found', 'err')

# create utility node
if cmd == 'utility':

  # define id of utility server
  utility_vm_id = cluster_id + 4 

  # check to see if already exists
  if utility_vm_id not in vms:
    kmsg(kname, 'creating utility node', 'sys')
    clone(utility_vm_id)
  else:
    kmsg(kname, 'utility node already exists')
  cluster_info()
