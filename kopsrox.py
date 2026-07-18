#!/usr/bin/env python3

# standard imports
import os, sys
sys.path[0:0] = ['lib/']

# kopsrox
from kopsrox_ini import init_kopsrox_ini
from kopsrox_kmsg import kmsg

# check file exists
if not os.path.isfile('kopsrox.ini'):
  init_kopsrox_ini()
  exit(0)

# kopsrox verbs and commands
cmds = {
  "image": {
    "info" : '',
    "create" : '',
    "update": '',
    "destroy": '',
  },
  "cluster": {
    "info" : '',
    "create" : '',
    "update" : '',
    "destroy" : '',
    'restore' : '',
  },
  "k3s": {
    "export-token" : '',
    "kubeconfig" : '',
    "check-config" : '',
    "kubectl" : 'cmd',
    "reload-kubevip" : '',
  },
  "etcd": {
    "snapshot" : '',
    "restore" : 'snapshot',
    "list" : '',
    "prune" : '',
  },
  "node": {
    "destroy" : 'hostname',
    "utility" : '',
    "terminal" : 'hostname',
    "ssh" : 'hostname',
    "reboot" : 'hostname',
    "k3s-uninstall" : 'hostname',
    "rejoin-slave" : 'hostname',
    "cluster-exec" : 'command',
  }
}

# create list of verbs
verbs = list(cmds)

# print list of verbs
def verbs_help():
  kmsg('kopsrox_usage', '[verb] [command]')
  print('verbs:')
  for kverb in verbs:
    print(f'- {kverb}')

# print verbs cmds
def cmds_help(verb):
  kmsg(f'kopsrox_{verb}', '[command]')
  print('commands:')
  for verb_cmd in list(cmds[verb]):

    # if command with required arg
    if cmds[verb][verb_cmd]:
      print(f'- {verb_cmd} [{cmds[verb][verb_cmd]}]')
    else:
      print(f'- {verb_cmd}')

# handle verb parameter
try:

  # check for 1st argument
  if sys.argv[1]:

    # map 1st arg to verb
    verb = sys.argv[1]

    # if verb not found in cmds dict
    if not verb in verbs:
      exit(0)

# verb not found or passed
except:
  verbs_help()
  exit(0)

# handle command
try:

  # 2nd arg = cmd
  if sys.argv[2]:
    cmd = sys.argv[2]

    # if cmd not in list of commands
    if not cmd in list(cmds[verb]):
      exit()

# cmd not found
except:
  cmds_help(verb)
  exit()

# handle commands with required args eg 'node ssh hostname'
try:
  if cmds[verb][cmd] and sys.argv[3]:
    pass
except:
  kmsg(f'kopsrox_{verb}', f'{cmd} [{cmds[verb][cmd]}]')
  exit(0)

# run passed verb
exec_verb = __import__('verb_' + verb)
