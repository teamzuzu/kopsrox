#!/usr/bin/env python3

# checks for lib/kopsrox_schema.py - pure, no proxmox needed
# run: ./dev/test_config.py

import sys, io, contextlib
sys.path[0:0] = ['lib/']

from configparser import ConfigParser
from kopsrox_schema import SCHEMA, validate, render_ini

# a real, well formed ssh public key - the schema default is an intentionally
# invalid placeholder ( check_sshkey rejects it ), so inject a valid one to test
# everything else that expects the defaults to validate
VALID_SSHKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINkh+xKu9IA3Q+TE3+BAwiid8b1ThwSh1aDjYCUOSo1/ test@kopsrox'

# render a fresh parser from the schema defaults ( with a valid ssh key )
def fresh_parser():
  rendered = io.StringIO()
  render_ini().write(rendered)
  parser = ConfigParser()
  parser.read_string(rendered.getvalue())
  parser.set('kopsrox', 'localsshkey', VALID_SSHKEY)
  return parser

# expect validate() to abort with exit code 1
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

# rendered ini contains every non-commented option and no commented ones
rendered_config = render_ini()
for entry in SCHEMA:
  if entry['commented']:
    assert not rendered_config.has_option('kopsrox', entry['name']), entry['name']
  else:
    assert rendered_config.has_option('kopsrox', entry['name']), f"{entry['name']} missing from rendered ini"

# defaults round-trip through validate
values = validate(fresh_parser())
assert values['cluster_name'] == 'mycluster', values['cluster_name']
assert values['cluster_id'] == 620 and type(values['cluster_id']) is int
assert values['masters'] == 1 and type(values['masters']) is int
assert values['region'] == '', 'commented s3_region should fall back to empty'
assert values['access_key'] == 'e3898d39d39id93', 'hyphenated ini name must map to access_key'
assert values['microvm_kernel'] == '/usr/share/pve-microvm/vmlinuz-kopsrox'
assert values['microvm_initrd'] == '/usr/share/pve-microvm/initrd-kopsrox'
assert values['extra_packages'] == 'nfs-common'

# every SCHEMA var is a valid python identifier ( they become module globals )
for entry in SCHEMA:
  assert entry['var'].isidentifier(), entry['var']

# negative cases - each must abort with exit 1
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

# blank allowed where blank is legal
parser = fresh_parser()
parser.set('kopsrox', 'extra_packages', '')
assert validate(parser)['extra_packages'] == ''

# kernel_version() - reads a bzImage boot header. importing kopsrox_config is
# safe here: nothing runs at import time, the proxmox work is all inside init()
from kopsrox_config import kernel_version

# synthesise a minimal bzImage header: 'HdrS' magic at 0x202 and a 2-byte LE
# offset at 0x20e pointing at the nul-terminated version string ( from 0x200 )
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

# a well formed header yields just the version token, not the whole build string
assert kernel_version(write_tmp(fake_bzimage('6.12.99'))) == '6.12.99'
assert kernel_version(write_tmp(fake_bzimage('6.12.22 (u@h) #1 SMP Wed Jul 29'))) == '6.12.22'

# unreadable / not a bzImage is "unknown" ( '' ), never an exception
assert kernel_version(write_tmp(fake_bzimage('6.12.99', magic = b'XXXX'))) == '', 'bad magic'
assert kernel_version(write_tmp(b'not a kernel')) == '', 'too short'
assert kernel_version('/nonexistent/vmlinuz-kopsrox') == '', 'missing file'
assert kernel_version(write_tmp(fake_bzimage('6.12.99', str_off = 0xfffe))) == '', 'offset past the buffer'

# and against the real kernel when this is running on a kopsrox node
import os
real = '/usr/share/pve-microvm/vmlinuz-kopsrox'
if os.path.isfile(real):
  assert kernel_version(real)[:1].isdigit(), f'unexpected version from {real}'

print('config schema tests OK')
