#!/usr/bin/env python3

# checks for lib/kopsrox_schema.py - pure, no proxmox needed

import sys, io, contextlib
sys.path[0:0] = ['lib/']

from configparser import ConfigParser
from kopsrox_schema import SCHEMA, validate, render_ini

VALID_SSHKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINkh+xKu9IA3Q+TE3+BAwiid8b1ThwSh1aDjYCUOSo1/ test@kopsrox'

def fresh_parser():
  rendered = io.StringIO()
  render_ini().write(rendered)
  parser = ConfigParser()
  parser.read_string(rendered.getvalue())
  parser.set('kopsrox', 'localsshkey', VALID_SSHKEY)
  return parser

def expect_abort(mutate):
  parser = fresh_parser()
  mutate(parser)
  try:
    with contextlib.redirect_stdout(io.StringIO()):
      validate(parser)
  except SystemExit as e:
    assert e.code == 1, f'abort should exit 1 not {e.code}'
    return
  assert False, 'expected validate() to abort'

rendered_config = render_ini()
for entry in SCHEMA:
  if entry['commented']:
    assert not rendered_config.has_option('kopsrox', entry['name']), entry['name']
  else:
    assert rendered_config.has_option('kopsrox', entry['name']), f"{entry['name']} missing from rendered ini"

values = validate(fresh_parser())
assert values['cluster_name'] == 'mycluster', values['cluster_name']
assert values['cluster_id'] == 620 and type(values['cluster_id']) is int
assert values['masters'] == 1 and type(values['masters']) is int
assert values['region'] == '', 'commented s3_region should fall back to empty'
assert values['access_key'] == 'e3898d39d39id93', 'hyphenated ini name must map to access_key'
assert values['microvm_kernel'] == '/usr/share/pve-microvm/vmlinuz-kopsrox'
assert values['microvm_initrd'] == '/usr/share/pve-microvm/initrd-kopsrox'
assert values['extra_packages'] == 'nfs-common'

for entry in SCHEMA:
  assert entry['var'].isidentifier(), entry['var']

expect_abort(lambda p: p.remove_option('kopsrox', 'cluster_name'))
expect_abort(lambda p: p.remove_option('kopsrox', 'k3s_version'))
expect_abort(lambda p: p.set('kopsrox', 'localuser', ''))
expect_abort(lambda p: p.set('kopsrox', 'vm_ram', 'x'))
expect_abort(lambda p: p.set('kopsrox', 'masters', '2'))
expect_abort(lambda p: p.set('kopsrox', 'cluster_id', '99'))
expect_abort(lambda p: p.set('kopsrox', 'localsshkey', 'notakey'))
expect_abort(lambda p: p.set('kopsrox', 'localsshkey', 'ssh-rsa cioieocieo'))            # schema placeholder
expect_abort(lambda p: p.set('kopsrox', 'localsshkey', 'ssh-ed25519'))                    # no blob
expect_abort(lambda p: p.set('kopsrox', 'localsshkey', 'ssh-ed25519 not!base64!'))        # bad base64
expect_abort(lambda p: p.set('kopsrox', 'localsshkey', 'ssh-rsa ' + VALID_SSHKEY.split()[1]))  # ed25519 blob under ssh-rsa type - type/blob mismatch
expect_abort(lambda p: p.set('kopsrox', 'vm_cpu', '0'))
expect_abort(lambda p: p.set('kopsrox', 'vm_disk', '10'))
expect_abort(lambda p: p.set('kopsrox', 'vm_ram', '1'))

parser = fresh_parser()
parser.set('kopsrox', 'extra_packages', '')
assert validate(parser)['extra_packages'] == ''

# importing kopsrox_config is safe - the proxmox work is all inside init()
from kopsrox_config import kernel_version

# minimal bzImage header: 'HdrS' at 0x202, LE offset at 0x20e -> string at +0x200
def fake_bzimage(version, magic = b'HdrS', str_off = 0x1000):
  buf = bytearray(0x10000)
  buf[0x202:0x206] = magic
  buf[0x20e:0x210] = str_off.to_bytes(2, 'little')
  ver = version.encode() + b'\x00'
  buf[0x200 + str_off:0x200 + str_off + len(ver)] = ver
  return bytes(buf)

def write_tmp(blob):
  import tempfile
  fh = tempfile.NamedTemporaryFile(delete = False)
  fh.write(blob)
  fh.close()
  return fh.name

assert kernel_version(write_tmp(fake_bzimage('6.12.99'))) == '6.12.99'
assert kernel_version(write_tmp(fake_bzimage('6.12.22 (u@h) #1 SMP Wed Jul 29'))) == '6.12.22'

assert kernel_version(write_tmp(fake_bzimage('6.12.99', magic = b'XXXX'))) == '', 'bad magic'
assert kernel_version(write_tmp(b'not a kernel')) == '', 'too short'
assert kernel_version('/nonexistent/vmlinuz-kopsrox') == '', 'missing file'
assert kernel_version(write_tmp(fake_bzimage('6.12.99', str_off = 0xfffe))) == '', 'offset past the buffer'

import os
real = '/usr/share/pve-microvm/vmlinuz-kopsrox'
if os.path.isfile(real):
  assert kernel_version(real)[:1].isdigit(), f'unexpected version from {real}'

# config_drift() - pure given the globals init() sets, so no proxmox needed
import kopsrox_config
from kopsrox_config import config_drift, config_hash, IMAGE_CONFIG_OPTS, CLUSTER_CONFIG_OPTS

def drift_globals(**over):
  base = dict(k3s_version = 'v1.34.1+k3s1', cloud_image_desc = 'k3s_version: v1.34.1+k3s1',
              oci_image = 'ubuntu:24.04', localuser = 'u', localpass = 'p', localsshkey = 'k',
              network_dns = '1.1.1.1', extra_packages = '', nfs_server = '', nfs_path = '',
              s3_endpoint = '', region = '', access_key = '', access_secret = '', bucket = '',
              masters = 1, workers = 1, kubelet_args = '')
  vars(kopsrox_config).update(base)
  vars(kopsrox_config).update(image_config_hash_baked = config_hash(IMAGE_CONFIG_OPTS),
                              cluster_config_hash_baked = config_hash(CLUSTER_CONFIG_OPTS))
  vars(kopsrox_config).update(over)

drift_globals()
assert config_drift() == [], 'matching baselines must report no drift'

drift_globals(k3s_version = 'v1.36.3+k3s1')
assert len(config_drift()) == 1 and 'k3s_version' in config_drift()[0], config_drift()

drift_globals(extra_packages = 'htop')
assert len(config_drift()) == 1 and 'image settings' in config_drift()[0], config_drift()

drift_globals(workers = 3)
assert len(config_drift()) == 1 and 'cluster settings' in config_drift()[0], config_drift()

drift_globals(k3s_version = 'v1.36.3+k3s1', extra_packages = 'htop', workers = 3)
assert len(config_drift()) == 3, config_drift()

# no baseline recorded - images/clusters built before the hashes existed never warn
drift_globals(extra_packages = 'htop', workers = 3,
              image_config_hash_baked = '', cluster_config_hash_baked = '')
assert config_drift() == [], 'absent baselines must stay silent'

# no image yet ( config check on a fresh install ) - empty description, nothing to compare
drift_globals(cloud_image_desc = '', image_config_hash_baked = '', cluster_config_hash_baked = '')
assert config_drift() == [], 'no image must stay silent'

# sudo_check() - kopsrox drives proxmox through sudo, so a password prompt would
# hang on captured stderr. patched here so the negative path is testable
from kopsrox_config import sudo_check

class FakeRun:
  def __init__(self, returncode): self.returncode = returncode

def with_sudo(euid, returncode):
  real_geteuid, real_run = kopsrox_config.os.geteuid, kopsrox_config.subprocess.run
  calls = []
  kopsrox_config.os.geteuid = lambda: euid
  kopsrox_config.subprocess.run = lambda *a, **kw: (calls.append(a[0]), FakeRun(returncode))[1]
  try:
    with contextlib.redirect_stdout(io.StringIO()):
      sudo_check('test_sudo')
    return 'ok', calls
  except SystemExit as e:
    return e.code, calls
  finally:
    kopsrox_config.os.geteuid, kopsrox_config.subprocess.run = real_geteuid, real_run

result, calls = with_sudo(euid = 0, returncode = 1)
assert result == 'ok', 'root must pass regardless of sudo'
assert calls == [], 'root must not spawn sudo at all'

result, calls = with_sudo(euid = 1000, returncode = 0)
assert result == 'ok', 'passwordless sudo must pass'
assert calls == [['sudo', '-n', 'true']], calls

result, calls = with_sudo(euid = 1000, returncode = 1)
assert result == 1, f'sudo needing a password must abort with exit 1, got {result}'

print('config schema tests OK')
