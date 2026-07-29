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
        "info": '',
        "create": '',
        "update": '',
        "destroy": '',
    },
    "cluster": {
        "info": '',
        "create": '',
        "update": '',
        "destroy": '',
        'restore': '',
    },
    "k3s": {
        "export-token": '',
        "kubeconfig": '',
        "check-config": '',
        "kubectl": 'cmd',
        "reload-kubevip": '',
        "upgrade": '',
    },
    "etcd": {
        "snapshot": '',
        "restore": 'snapshot',
        "list": '',
        "prune": '',
    },
    "node": {
        "destroy": 'hostname',
        "utility": '',
        "terminal": 'hostname',
        "ssh": 'hostname',
        "reboot": 'hostname',
        "k3s-uninstall": 'hostname',
        "rejoin-slave": 'hostname',
        "cluster-exec": 'command',
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

# no verb passed - print help
if len(sys.argv) < 2:
    verbs_help()
    exit(0)
verb = sys.argv[1]

# unknown verb is an error
if verb not in verbs:
    verbs_help()
    exit(1)

# no command passed - print the verb help
if len(sys.argv) < 3:
    cmds_help(verb)
    exit(0)
cmd = sys.argv[2]

# unknown command is an error
if cmd not in cmds[verb]:
    cmds_help(verb)
    exit(1)

# handle commands with required args eg 'node ssh hostname'
if cmds[verb][cmd] and len(sys.argv) < 4:
    kmsg(f'kopsrox_{verb}', f'{cmd} [{cmds[verb][cmd]}]')
    exit(1)

# argument for commands that take one ( validated above )
# joined ( not just argv[3] ) so unquoted multi-word commands keep working -
# eg './kopsrox.py k3s kubectl get pods -A' or 'node cluster-exec <command>'
arg = ' '.join(sys.argv[3:]) if len(sys.argv) > 3 else None

# staged config checks, then dispatch
import kopsrox_config
kopsrox_config.init(verb, cmd)
run_verb = __import__('verb_' + verb)
run_verb.run(cmd, arg)
