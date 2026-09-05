#!/usr/bin/env python3

import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from configparser import ConfigParser
from datetime import datetime
from urllib.parse import unquote

import requests

from kopsrox_kmsg import kmsg, kabort, kstep, kplan, kplan_tick
from kopsrox_schema import validate


# local proxmox cli via sudo. qa_exec passes check=False - qm guest exec exits 0
# even when the guest command fails
def pve_run(args: list[str], input: str | None = None, timeout: int | None = None,
            check: bool = True, kname: str = 'proxmox_cli') -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(['sudo'] + args, text = True, capture_output = True, input = input, timeout = timeout)
    except Exception as e:
        kabort(kname, f'failed to run: {" ".join(args)}\n{e}')
    if check and cp.returncode != 0:
        kabort(kname, f'command failed: {" ".join(args)}\n{(cp.stderr or cp.stdout).strip()}')
    return cp


MICROVM_RAW_URL = 'https://raw.githubusercontent.com/rcarmo/pve-microvm/main'

# ini options that only take effect via a rebuild. a hash of each group is recorded
# in the i0 / m1 descriptions and compared in init() - secrets are stored hashed
IMAGE_CONFIG_OPTS = ('oci_image', 'localuser', 'localpass', 'localsshkey', 'network_dns', 'extra_packages', 'nfs_server', 'nfs_path')
CLUSTER_CONFIG_OPTS = ('s3_endpoint', 'region', 'access_key', 'access_secret', 'bucket', 'masters', 'workers', 'kubelet_args')

def config_hash(names) -> str:
    g = globals()
    blob = '\n'.join(f'{n}={g.get(n, "")}' for n in names)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# version out of a bzImage header - the hand-built kernel has no dpkg entry
def kernel_version(path: str) -> str:
    try:
        with open(path, 'rb') as kernel_file:
            head = kernel_file.read(0x10000)
        if head[0x202:0x206] != b'HdrS':
            return ''
        off = int.from_bytes(head[0x20e:0x210], 'little') + 0x200
        return head[off:head.index(b'\x00', off)].decode().split()[0]
    except Exception:
        return ''


def kopsrox_img() -> str | bool:

    if globals().get('cloud_image_disk'):
        return cloud_image_disk

    for line in pve_run(['pvesm', 'list', proxmox_storage]).stdout.splitlines():
        parts = line.split()
        if parts and re.search(f'{cluster_id}-disk-0', parts[0]):
            return parts[0]

    return False

def list_kopsrox_vm() -> dict[int, str]:

    vmids = {}
    for line in pve_run(['bash', '-c', 'ls /etc/pve/qemu-server/*.conf 2>/dev/null']).stdout.splitlines():
        base = line.rsplit('/', 1)[-1]
        if not (base.endswith('.conf') and base[:-5].isdigit()):
            continue
        vmid = int(base[:-5])

        if (vmid >= cluster_id) and (vmid < (cluster_id + 10)):
            vmids[vmid] = proxmox_node

    return dict(sorted(vmids.items()))

def get_k3s_token() -> str | None:
    token_fname = f'{cluster_name}.k3stoken'
    if os.path.isfile(token_fname):
        raw = open(token_fname, "r").read()
        # a stray newline makes export_k3s_token report 'passwords different'
        if '\r' in raw or '\n' in raw:
            kabort(f'{cluster_name}_token', f'{token_fname} contains a line break or carriage return - '
                   f'a k3s token must be a single line with no trailing newline '
                   f"( fix: printf '%s' \"$(cat {token_fname})\" > {token_fname} )")
        return raw

def vmip(vmid: int) -> str:
    ip = f'{network_base}{(network_ip_prefix + (vmid - cluster_id))}'
    return ip

def local_exec(cmd: str) -> subprocess.CompletedProcess:
    cmd_run = subprocess.run(['bash', "-c", cmd], text=True, capture_output=True)

    if (cmd_run.returncode == 1 or cmd_run.stderr != ''):
        kabort('local_exec-process-error', f'{cmd}\n{cmd_run.stderr.strip()}')
    return cmd_run

def image_info() -> None:
    kname = f'image_'
    kmsg(f'{kname}desc', cloud_image_desc)
    kmsg(f'{kname}storage', f'{kopsrox_img()}')


def init(verb: str, cmd: str) -> None:
    g = globals()

    kopsrox_config = ConfigParser()
    kopsrox_config.read('kopsrox.ini')
    g.update(validate(kopsrox_config))

    g['kname'] = f'{g["cluster_name"]}_config-check'

    g['masterid'] = int(g['cluster_id']) + 1

    network_octs = g['network_ip'].split('.')
    g['network_base'] = f'{network_octs[0]}.{network_octs[1]}.{network_octs[2]}.'
    g['network_ip_prefix'] = int(network_octs[-1])

    g['region_string'] = ''
    if g['region']:
        g['region_string'] = g['region']

    g['suffixes'] = ['-i0', '-m1', '-m2', '-m3', '-u1', '-w1', '-w2', '-w3', '-w4', '-w5']
    g['vmnames'] = {
        g['cluster_id'] + i: f"{g['cluster_name']}{suffix}"
        for i, suffix in enumerate(g['suffixes'])
    }

    g['proxmox_node'] = socket.gethostname().split('.')[0]
    proxmox_node = g['proxmox_node']

    if not os.path.isdir(f'/etc/pve/nodes/{proxmox_node}'):
        kabort(g['kname'], f'this host ({proxmox_node}) does not look like a proxmox node - '
               'kopsrox must run on the pve node itself ( qm / pvesm / pvesh )')

    cluster_id = g['cluster_id']

    # all discovery in ONE ~0.02s sudo spawn; a pvesh / qm equivalent is ~1.4s each
    disc = pve_run(['bash', '-c',
        'echo "##STORAGE"; cat /etc/pve/storage.cfg 2>/dev/null; '
        'echo "##VMS"; '
        'for f in /etc/pve/qemu-server/*.conf; do [ -e "$f" ] || continue; '
        'v=$(basename "$f" .conf); p=/var/run/qemu-server/$v.pid; '
        'if [ -f "$p" ] && kill -0 "$(cat "$p" 2>/dev/null)" 2>/dev/null; then s=running; else s=stopped; fi; '
        'echo "$v $s"; done; '
        f'echo "##TEMPLATECONF"; cat /etc/pve/qemu-server/{cluster_id}.conf 2>/dev/null; '
        f'echo "##MASTERCONF"; cat /etc/pve/qemu-server/{cluster_id + 1}.conf 2>/dev/null; '
        # both confs are legitimately absent, so exit 0 or a failed cat kaborts
        'exit 0'],
        kname = g['kname']).stdout

    storage_block = disc.split('##STORAGE', 1)[-1].split('##VMS', 1)[0]
    vms_block = disc.split('##VMS', 1)[-1].split('##TEMPLATECONF', 1)[0]
    g['template_conf'], _, master_conf = disc.split('##TEMPLATECONF', 1)[-1].partition('##MASTERCONF')

    storage_names = re.findall(r'^\w+:\s+(\S+)', storage_block, re.MULTILINE)
    if g['proxmox_storage'] not in storage_names:
        kabort(g['kname'], f'{g["proxmox_storage"]} storage not found in /etc/pve/storage.cfg on {proxmox_node}')

    disc_vms = {}
    for line in vms_block.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        vmid = int(parts[0])
        if cluster_id <= vmid < cluster_id + 10:
            disc_vms[vmid] = {'vmid': vmid, 'name': g['vmnames'].get(vmid, str(vmid)), 'status': parts[1]}
    g['disc_vms'] = disc_vms

    g['vms'] = {vmid: proxmox_node for vmid in sorted(disc_vms)}
    vms = g['vms']

    if not (verb == 'image' and cmd == 'create'):
        if cluster_id not in vms:
            kabort(g['kname'], f'{g["cluster_name"]} image not found - please run "kopsrox image create"')

    cfg = g['template_conf']
    desc_lines = [unquote(line[1:]) for line in cfg.splitlines() if line.startswith('#')]
    g['cloud_image_desc'] = '\n'.join(desc_lines)
    g['cloud_image_disk'] = ''
    disk_match = re.search(rf'(\S+:\S*{cluster_id}-disk-0)', cfg)
    if disk_match:
        g['cloud_image_disk'] = disk_match.group(1)

    # drift baselines - empty for older builds, keeping the notices below silent
    master_desc = '\n'.join(unquote(line[1:]) for line in master_conf.splitlines() if line.startswith('#'))
    image_hash_match = re.search(r'config_hash: (\S+)', g['cloud_image_desc'])
    cluster_hash_match = re.search(r'config_hash: (\S+)', master_desc)
    g['image_config_hash_baked'] = image_hash_match.group(1) if image_hash_match else ''
    g['cluster_config_hash_baked'] = cluster_hash_match.group(1) if cluster_hash_match else ''

    # the ini changes nothing until image create rebuilds, so warn on a mismatch
    image_k3s_match = re.search(r'k3s_version: (\S+)', g['cloud_image_desc'])
    if not (verb == 'image' and cmd == 'create') and image_k3s_match and image_k3s_match.group(1) != g['k3s_version']:
        kmsg(g['kname'], f'kopsrox.ini k3s_version ({g["k3s_version"]}) differs from the image '
             f'({image_k3s_match.group(1)}) - run "image create" to rebuild, then "k3s upgrade" '
             f'to apply it to the running cluster', 'sys')

    if g['image_config_hash_baked'] and not (verb == 'image' and cmd == 'create') \
            and g['image_config_hash_baked'] != config_hash(IMAGE_CONFIG_OPTS):
        kmsg(g['kname'], 'kopsrox.ini image settings ( oci_image, localuser/pass/sshkey, network_dns, '
             'extra_packages, nfs_* ) changed since the image was built - run "image create" to apply', 'sys')

    if g['cluster_config_hash_baked'] and not (verb == 'cluster' and cmd in ('create', 'update', 'restore')) \
            and g['cluster_config_hash_baked'] != config_hash(CLUSTER_CONFIG_OPTS):
        kmsg(g['kname'], 'kopsrox.ini cluster settings ( s3_*, masters, workers, kubelet_args ) changed '
             'since the cluster was built - run "cluster update" to apply', 'sys')

    if verb in ['cluster', 'k3s', 'etcd', 'node']:
        for vmid in vms:
            if vmid != cluster_id and disc_vms[vmid].get('status') == 'stopped':
                kmsg(g['kname'], f'powering on {disc_vms[vmid].get("name")}', 'sys')
                pve_run(['qm', 'start', str(vmid)], kname = g['kname'])

    # agent-alive as a proxy for k3s-alive. a ~1.4s spawn, so only where consumed
    g['conf_check_master_up'] = False
    masterid = g['masterid']
    if (verb == 'cluster' or (verb == 'image' and cmd == 'create')) and disc_vms.get(masterid, {}).get('status') == 'running':
        if pve_run(['qm', 'agent', str(masterid), 'ping'], check = False, kname = g['kname']).returncode == 0:
            g['conf_check_master_up'] = True

    # normalise "sdn/<zone>/<vnet>" to the bare vnet - needed by every verb
    g['sdn_zone'] = ''
    if re.search('sdn/', g['network_bridge']):
        sdn_params = g['network_bridge'].split('/')
        if len(sdn_params) != 3 or not sdn_params[1] or not sdn_params[2]:
            kabort(g['kname'], f'unable to parse sdn config: "{g["network_bridge"]}"')
        g['sdn_zone'], g['network_bridge'] = sdn_params[1], sdn_params[2]

    # build prerequisites only - latency image info / destroy should not pay
    if verb == 'image' and cmd == 'create':

        # 0.3.24 floor: .22 is the layout the patches anchor on, .23 fixed
        # linked-clone disk format ( the raw fallback corrupts qcow2 ), .24
        # drops the rival guest-agent unit
        g['microvm_ver'] = subprocess.run(['bash', '-c', "dpkg-query -W -f '${Version}' pve-microvm 2>/dev/null || echo none"], text=True, capture_output=True).stdout.strip()
        microvm_ver = g['microvm_ver']
        if microvm_ver == 'none':
            kabort(g['kname'], 'pve-microvm is not installed - see README.md')
        microvm_installed = tuple(map(int, microvm_ver.split('-')[0].split('.')))
        if microvm_installed < (0, 3, 24):
            kabort(g['kname'], f'pve-microvm {microvm_ver} is too old - kopsrox needs 0.3.24 or later')

        try:
            microvm_latest_tag = requests.get('https://api.github.com/repos/rcarmo/pve-microvm/releases/latest', timeout=3).json()['tag_name']
            if tuple(map(int, microvm_latest_tag.lstrip('v').split('.'))) > microvm_installed:
                kmsg(g['kname'], f'pve-microvm {microvm_latest_tag} is available ( installed: {microvm_ver} )', 'sys')
        except Exception:
            pass

        # same for the guest kernel - unrelated to the proxmox host kernel
        try:
            microvm_builder = requests.get(f'{MICROVM_RAW_URL}/kernel/build-kernel.sh', timeout=3).text
            kernel_latest = re.search(r'DEFAULT_VERSION="([^"]+)"', microvm_builder).group(1)
            kernel_built = kernel_version(g['microvm_kernel'])
            if tuple(map(int, kernel_latest.split('.'))) > tuple(map(int, kernel_built.split('.'))):
                kmsg(g['kname'], f'pve-microvm kernel {kernel_latest} is available ( kopsrox kernel: '
                     f'{kernel_built} ) - run dev/build-kopsrox-kernel.sh, then "image create"', 'sys')
        except Exception:
            pass

        network_bridge = g['network_bridge']
        if not g['conf_check_master_up']:
            if not g['sdn_zone']:
                bridges = json.loads(pve_run(['pvesh', 'get', f'/nodes/{proxmox_node}/network',
                                              '--type', 'bridge', '--output-format', 'json'], kname = g['kname']).stdout)
                discovered_bridges = [b.get('iface') for b in bridges]
            else:
                content = json.loads(pve_run(['pvesh', 'get', f'/nodes/{proxmox_node}/sdn/zones/{g["sdn_zone"]}/content',
                                              '--output-format', 'json'], kname = g['kname']).stdout)
                discovered_bridges = [b.get('vnet') for b in content]

            if network_bridge not in discovered_bridges:
                kabort(g['kname'], f'"{network_bridge}" not found. valid bridges: {discovered_bridges}')
