#!/usr/bin/env python3

# kopsrox config schema - single source of truth for every kopsrox.ini option
# pure module: no proxmox, no argv, no side effects
# imported by kopsrox_config ( validation ) and dev/test_config.py; also renders
# and writes the default kopsrox.ini itself ( render_ini / init_kopsrox_ini )
# it must never import kopsrox_config - the default ini is generated exactly when kopsrox.ini is missing

import base64, struct

from configparser import ConfigParser
from kopsrox_kmsg import kabort

# validators - called as check(kname, value) after type coercion
def check_cluster_id(kname: str, value: int) -> None:
    if value < 100:
        kabort(kname, f'cluster_id is too low - should be over 100')

def check_vm_disk(kname: str, value: int) -> None:
    if value < 20:
        kabort(kname, f'vm_ - kopsrox vms need 20G disk')

def check_vm_cpu(kname: str, value: int) -> None:
    if value < 1:
        kabort(kname, f'vm_ - kopsrox vms at least 1 cpu')

def check_vm_ram(kname: str, value: int) -> None:
    if value < 2:
        kabort(kname, f'vm_ram - kopsrox vms need 2G RAM')

# recognised openssh public-key type prefixes ( authorized_keys format )
SSH_KEY_TYPES = ('ssh-rsa', 'ssh-ed25519', 'ssh-dss',
                 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
                 'sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com')

# validate a real openssh public key, not just an 'ssh-' prefix: the key type
# must be recognised, the base64 blob must decode, and the algorithm name encoded
# in the blob ( first length-prefixed field ) must match the type - this catches
# a truncated / corrupt / mis-pasted key that a prefix check would wave through
def check_sshkey(kname: str, value: str) -> None:
    err = ('[kopsrox]/localsshkey - not a valid ssh public key ( openssh '
           'authorized_keys format eg "ssh-ed25519 AAAAC3... user@host" )')
    parts = value.split()
    if len(parts) < 2 or parts[0] not in SSH_KEY_TYPES:
        kabort(kname, err)
    keytype, blob = parts[0], parts[1]
    try:
        raw = base64.b64decode(blob, validate = True)
        length = struct.unpack('>I', raw[:4])[0]
        embedded = raw[4:4 + length].decode()
    except Exception:
        kabort(kname, err)
    if embedded != keytype:
        kabort(kname, err)

def check_masters(kname: str, value: int) -> None:
    if not (value == 1 or value == 3):
        kabort(kname, f'[cluster] - masters: only 1 or 3 masters supported. You have: {value}')

# one option definition
# comment: None, a string or a list of strings - rendered as ; lines above the option
# commented: option ships commented out and resolves to default when absent
# var: the module global the value lands in ( ini names with hyphens need one )
# ini_value: literal text written to the default ini when it differs from default
def opt(name, comment, default, kind = str, blank_ok = False, commented = False, check = None, var = None, ini_value = None) -> dict:
    return {'name': name, 'comment': comment, 'default': default, 'kind': kind, 'blank_ok': blank_ok,
            'commented': commented, 'check': check, 'var': var or name, 'ini_value': ini_value}

# every kopsrox.ini option in ini order - adding an option means adding one entry here
SCHEMA = [
    opt('proxmox_endpoint', 'domain or IP to access proxmox', '127.0.0.1'),
    opt('proxmox_api_port', 'api port ( usually 8006 ) ', '8006', kind = int),
    opt('proxmox_user', 'username to connect with / owner of the API token', 'root@pam'),
    opt('proxmox_token_name', 'name of api token', 'kopsrox'),
    opt('proxmox_token_value', 'text of api key', 'xxxxxxxxxxxxx'),
    opt('proxmox_node', 'the proxmox node that you will run kopsrox on - the image and all nodes are created on this host', 'proxmox'),
    opt('proxmox_storage', 'the proxmox storage to use for kopsrox - needs to be available on the proxmox node', 'local-lvm'),
    opt('oci_image', 'the OCI image used to build the microvm template ( via pve-microvm-template )', 'ubuntu:26.04'),
    opt('microvm_kernel', 'kernel/initrd used to boot kopsrox microvms - built with dev/build-kopsrox-kernel.sh',
        '/usr/share/pve-microvm/vmlinuz-kopsrox', commented = True),
    opt('microvm_initrd', None, '/usr/share/pve-microvm/initrd-kopsrox', commented = True),
    opt('extra_packages', 'comma seperated list of extra packages installed into each node when created ', 'nfs-common', blank_ok = True),
    opt('vm_disk', 'size of vm disk in Gib ', '20', kind = int, check = check_vm_disk),
    opt('vm_cpu', 'number of cpu cores ', '1', kind = int, check = check_vm_cpu),
    opt('vm_ram', 'amount of ram in Gib ', '2', kind = int, check = check_vm_ram),
    opt('localuser', 'username for the user created in each node ( via the guest agent )', 'user'),
    opt('localpass', 'password for the created user', 'admin'),
    opt('localsshkey', 'ssh public key for the created user ( required )', 'ssh-rsa cioieocieo', check = check_sshkey),
    opt('network_bridge', ['network bridge to use with kopsrox',
        'a proxmox sdn can be used by specifying the zone and vnet like this: sdn/zone/vnet'], 'vmbr0'),
    opt('network_ip', 'first ip of the ip range used for this kopsrox cluster', '192.168.0.160'),
    opt('network_mask', '/24 is 255.255.255.0', '24'),
    opt('network_gw', 'default gateway for the network ( needs to provide internet access ) ', '192.168.0.1'),
    opt('network_dns', 'dns server for network', '192.168.0.1'),
    opt('network_mtu', ['interface mtu applied inside each node ', 'set to 1450 if using sdn '], '1500', kind = int),
    opt('nfs_server', ['ip / hostname of an external nfs server to back a dynamic \'nfs\' storageclass',
        'leave blank to disable ( local-path stays the only / default storageclass )',
        'changing this needs an image update to take effect ( baked into the image )'],
        '', blank_ok = True, commented = True),
    opt('nfs_path', 'exported path on the nfs server ( eg /export/kopsrox )', '/export/kopsrox', commented = True),
    opt('cluster_id', 'id for the cluster vm\'s eg from 620 - 630', '620', kind = int, check = check_cluster_id),
    opt('cluster_name', 'name of the cluster', 'mycluster'),
    opt('masters', 'number of masters nodes 1 or 3', '1', kind = int, check = check_masters),
    opt('workers', 'number of workers nodes 1 to 5', '1', kind = int),
    opt('k3s_version', 'k3s version', 'v1.35.6+k3s1'),
    opt('kubelet_args', ['comma separated list of kubelet args applied to every node ( eg max-pods=250 )',
        'blank for none - takes effect on the next node join, no image update needed'], '', blank_ok = True, commented = True),
    opt('s3_endpoint', 's3 endpoint', 'kopsrox'),
    opt('s3_region', 's3 region - leave as \'\' for no region', '', commented = True, var = 'region', ini_value = '\'\''),
    opt('s3_access-key', 's3 access key', 'e3898d39d39id93', var = 'access_key'),
    opt('s3_access-secret', 's3 access secret', 'ioewioeiowe', var = 'access_secret'),
    opt('s3_bucket', 's3 bucket', 'kopsrox-backup', var = 'bucket'),
]

# validate a parsed kopsrox.ini against the schema
# returns { var: typed value } - aborts with todays messages on any problem
def validate(parser: ConfigParser) -> dict:

    # resolve cluster_name first so later messages carry it
    kname = 'config_check'
    values = {}

    def resolve(entry):
        name = entry['name']

        # commented options fall back to their default when absent
        if entry['commented']:
            raw = parser.get('kopsrox', name, fallback = entry['default'])
        else:
            if not parser.has_option('kopsrox', name):
                kabort(kname, f'{name} is missing in kopsrox.ini')
            raw = parser.get('kopsrox', name)
            if raw == '' and not entry['blank_ok']:
                kabort(kname, f'{name} - a value is required')

        # int options
        if entry['kind'] is int:
            try:
                raw = int(raw)
            except Exception:
                kabort(kname, f'{name} should be numeric: {raw}')

        # option specific check
        if entry['check']:
            entry['check'](kname, raw)
        return raw

    by_name = {entry['name']: entry for entry in SCHEMA}
    values['cluster_name'] = resolve(by_name['cluster_name'])
    kname = f"{values['cluster_name']}_config-check"

    for entry in SCHEMA:
        if entry['name'] == 'cluster_name':
            continue
        values[entry['var']] = resolve(entry)

    return values

# build the default ini from the schema - same allow_no_value comment
# technique the old hand written generator used, so the format is unchanged
def render_ini() -> ConfigParser:
    config = ConfigParser(allow_no_value = True)
    ks = 'kopsrox'
    config.add_section(ks)

    for entry in SCHEMA:
        comments = entry['comment']
        if isinstance(comments, str):
            comments = [comments]
        for comment in comments or []:
            config.set(ks, f'; {comment}')
        name = f"# {entry['name']}" if entry['commented'] else entry['name']
        value = entry['ini_value'] if entry['ini_value'] is not None else str(entry['default'])
        config.set(ks, name, value)

    return config

# write the default kopsrox.ini - called by kopsrox.py when none exists yet
def init_kopsrox_ini() -> None:
    with open('kopsrox.ini', 'w') as cfile:
        render_ini().write(cfile)
    print('created kopsrox.ini please edit for your setup')
