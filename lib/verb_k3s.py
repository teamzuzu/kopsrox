#!/usr/bin/env python3

# functions
from kopsrox_k3s import * 

# passed command
cmd = sys.argv[2]

# map arg if passed
try:
  arg = sys.argv[3]
except:
  pass

# define kname
kname = 'k3s_'+cmd

# k3s token
if cmd == 'export-token':
  export_k3s_token()

# export kubeconfig to file
if cmd == 'kubeconfig':
  kubeconfig()

# check k3s config
if cmd == 'check-config':
  k3s_check_config()

# reload kubevip
if cmd == 'reload-kubevip':
  reload_kubevip()

# kubectl
if cmd == 'kubectl':

  # init kcmd
  kcmd= ''

  # convert command line into string
  for arg in sys.argv[1:]:
    if ' ' in arg:

      # Put the quotes back in
      kcmd+='"{}" '.format(arg) ;
    else:

      # Assume no space => no quotes
      kcmd+="{} ".format(arg) ;

  # remove first 2 commands
  kcmd = kcmd.replace('k3s kubectl ','')

  # run command and show output
  kmsg('kubectl_cmd', kcmd, 'sys')
  print(kubectl(kcmd))
