#!/usr/bin/env python3

"""Check prune scope without contacting a cluster or S3."""

import importlib.util
import shlex
import sys
from types import ModuleType
from unittest.mock import Mock, patch


config = ModuleType('kopsrox_config')
config.bucket = 'shared-backups'
config.cluster_name = 'anchovy'
config.get_k3s_token = Mock()
config.masterid = 101
config.s3_endpoint = 's3.example.com'
config.vms = {101: {}}
messages = ModuleType('kopsrox_kmsg')
messages.kabort = Mock(side_effect=AssertionError)
messages.kmsg = Mock()
proxmox = ModuleType('kopsrox_proxmox')
proxmox.qa_exec = Mock(return_value='')

with patch.dict(sys.modules, kopsrox_config=config,
                kopsrox_kmsg=messages, kopsrox_proxmox=proxmox):
    spec = importlib.util.spec_from_file_location('verb_etcd', 'lib/verb_etcd.py')
    etcd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(etcd)

with patch.object(etcd, '_etcd_checks', return_value=''), patch('builtins.exit', side_effect=SystemExit):
    try:
        etcd.run('prune')
    except SystemExit:
        pass

command = proxmox.qa_exec.call_args.args[1]
prefix = shlex.split(command)[4]
assert prefix == 'kopsrox-anchovy-m', command
assert 'kopsrox-anchovy-m1-1730000000'.startswith(prefix)
assert 'kopsrox-anchovy-m3-1730000000'.startswith(prefix)
assert not 'kopsrox-other-m1-1730000000'.startswith(prefix)
assert not 'kopsrox-anchovy2-m1-1730000000'.startswith(prefix)
assert not 'kopsrox-anchovy-dev-m1-1730000000'.startswith(prefix)
print('etcd prune scope checks passed')
