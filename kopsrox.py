#!/usr/bin/env python3

import os, sys
sys.path[0:0] = ['lib/']

from kopsrox_schema import init_kopsrox_ini
from kopsrox_kmsg import kmsg

if not os.path.isfile('kopsrox.ini'):
    init_kopsrox_ini()
    exit(0)

# kopsrox verbs and commands
cmds = {
    "image": {
        "info": '',
        "create": '',
        "destroy": '',
    },
    "cluster": {
        "info": '',
        "create": '',
        "update": '',
        "destroy": '',
        'restore': '',
        "exec": 'command',
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
    }
}

verbs = list(cmds)

def verbs_help():
    kmsg('kopsrox_usage', '[verb] [command]')
    print('verbs:')
    for kverb in verbs:
        print(f'- {kverb}')

def cmds_help(verb):
    kmsg(f'kopsrox_{verb}', '[command]')
    print('commands:')
    for verb_cmd in list(cmds[verb]):

        if cmds[verb][verb_cmd]:
            print(f'- {verb_cmd} [{cmds[verb][verb_cmd]}]')
        else:
            print(f'- {verb_cmd}')

if len(sys.argv) < 2:
    verbs_help()
    exit(0)
verb = sys.argv[1]

if verb not in verbs:
    verbs_help()
    exit(1)

if len(sys.argv) < 3:
    cmds_help(verb)
    exit(0)
cmd = sys.argv[2]

if cmd not in cmds[verb]:
    cmds_help(verb)
    exit(1)

if cmds[verb][cmd] and len(sys.argv) < 4:
    kmsg(f'kopsrox_{verb}', f'{cmd} [{cmds[verb][cmd]}]')
    exit(1)

# joined, not just argv[3], so unquoted multi-word commands keep working -
# eg 'k3s kubectl get pods -A' or 'cluster exec <command>'
arg = ' '.join(sys.argv[3:]) if len(sys.argv) > 3 else None

# staged config checks, then dispatch
import kopsrox_config
kopsrox_config.init(verb, cmd)
run_verb = __import__('verb_' + verb)
run_verb.run(cmd, arg)
