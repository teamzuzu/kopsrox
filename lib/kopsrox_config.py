#!/usr/bin/env python3

# external imports
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


# run a local proxmox cli command ( qm / pvesm / pvesh ) via sudo - kopsrox runs
# on the proxmox node itself so everything goes through these tools, not the http
# api. args is an argv list ( not a shell string ) so payloads with quotes and
# newlines need no escaping. returns the CompletedProcess; with check=True a
# non-zero exit kaborts. NB qm guest exec is an exception to check=True: it exits
# 0 at the qm level even when the in-guest command fails, so qa_exec parses the
# json exit code itself and calls this with check=False
def pve_run(args: list[str], input: str | None = None, timeout: int | None = None,
            check: bool = True, kname: str = 'proxmox_cli') -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(['sudo'] + args, text = True, capture_output = True, input = input, timeout = timeout)
    except Exception as e:
        kabort(kname, f'failed to run: {" ".join(args)}\n{e}')
    if check and cp.returncode != 0:
        kabort(kname, f'command failed: {" ".join(args)}\n{(cp.stderr or cp.stdout).strip()}')
    return cp


# config-drift detection: the ini options that only take effect via a rebuild.
# IMAGE_CONFIG_OPTS are baked into the image ( change -> image create ); the k3s
# version is deliberately excluded, it keeps its own more specific notice.
# CLUSTER_CONFIG_OPTS are applied to the running cluster at join / reconcile
# ( change -> cluster update ). a hash of each group is recorded at image create
# / cluster create-update ( in the i0 / m1 vm descriptions ) and compared here on
# later runs - see init(). secrets ( passwords, keys, s3 creds ) only ever appear
# hashed, never in plain text in a description
IMAGE_CONFIG_OPTS = ('oci_image', 'localuser', 'localpass', 'localsshkey', 'network_dns', 'extra_packages', 'nfs_server', 'nfs_path')
CLUSTER_CONFIG_OPTS = ('s3_endpoint', 'region', 'access_key', 'access_secret', 'bucket', 'masters', 'workers', 'kubelet_args')

# short stable hash of the given option globals ( order-fixed, name-tagged )
def config_hash(names) -> str:
    g = globals()
    blob = '\n'.join(f'{n}={g.get(n, "")}' for n in names)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# look up kopsrox_img name
def kopsrox_img() -> str | bool:

    # init already captured the template disk volid from qm config - reuse it
    # rather than paying another ~1.4s pvesm spawn
    if globals().get('cloud_image_disk'):
        return cloud_image_disk

    # fall back to pvesm list ( eg image not discovered during init )
    for line in pve_run(['pvesm', 'list', proxmox_storage]).stdout.splitlines():
        parts = line.split()
        if parts and re.search(f'{cluster_id}-disk-0', parts[0]):
            return parts[0]

    # unable to find image name
    return False

# return dict of kopsrox vms by node ( always the local node ) - a fresh read
# ( state changes mid cluster op ), straight from the local qemu-server conf dir
# so it is a cheap sudo spawn, not a ~1.4s qm list
def list_kopsrox_vm() -> dict[int, str]:

    # init dict
    vmids = {}

    # each /etc/pve/qemu-server/<vmid>.conf is a vm on this node
    for line in pve_run(['bash', '-c', 'ls /etc/pve/qemu-server/*.conf 2>/dev/null']).stdout.splitlines():
        base = line.rsplit('/', 1)[-1]
        if not (base.endswith('.conf') and base[:-5].isdigit()):
            continue
        vmid = int(base[:-5])

        # if vmid is in kopsrox config range ie between cluster_id and cluster_id + 10
        if (vmid >= cluster_id) and (vmid < (cluster_id + 10)):
            vmids[vmid] = proxmox_node

    # return sorted dict
    return dict(sorted(vmids.items()))

# get token if it exists
def get_k3s_token() -> str | None:
    token_fname = f'{cluster_name}.k3stoken'
    if os.path.isfile(token_fname):
        raw = open(token_fname, "r").read()
        # a k3s token is a single line ( K10<ca-hash>::server:<password> ).
        # an imported/hand-edited file often carries a trailing newline or a
        # windows carriage return - k3s itself strips it so the cluster comes
        # up fine, but the raw read here keeps it, so export_k3s_token's exact
        # and password comparisons fail with a misleading 'passwords different'.
        # flag it up front instead, before it bootstraps anything
        if '\r' in raw or '\n' in raw:
            kabort(f'{cluster_name}_token', f'{token_fname} contains a line break or carriage return - '
                   f'a k3s token must be a single line with no trailing newline '
                   f"( fix: printf '%s' \"$(cat {token_fname})\" > {token_fname} )")
        return raw

# return ip for vmid
def vmip(vmid: int) -> str:
    # last number of network + ( vmid - cluster_id )
    # eg 160 + ( 601 - 600 )  = 161
    ip = f'{network_base}{(network_ip_prefix + (vmid - cluster_id))}'
    return ip

# run local os process
def local_exec(cmd: str) -> subprocess.CompletedProcess:
    cmd_run = subprocess.run(['bash', "-c", cmd], text=True, capture_output=True)

    # if return code 1 or any stderr
    if (cmd_run.returncode == 1 or cmd_run.stderr != ''):
        kabort('local_exec-process-error', f'{cmd}\n{cmd_run.stderr.strip()}')
    return cmd_run

# print image info
def image_info() -> None:
    kname = f'image_'
    kmsg(f'{kname}desc', cloud_image_desc)
    kmsg(f'{kname}storage', f'{kopsrox_img()}')


def init(verb: str, cmd: str) -> None:
    # everything the old module did at import time, in the same order -
    # values land as module attributes via globals() so consumers can
    # 'from kopsrox_config import cluster_name' after init has run
    g = globals()

    # read and validate kopsrox.ini against the schema - injects every option
    # as a module global ( see SCHEMA in kopsrox_schema.py for the names )
    kopsrox_config = ConfigParser()
    kopsrox_config.read('kopsrox.ini')
    g.update(validate(kopsrox_config))

    # kname and the verb being run
    g['kname'] = f'{g["cluster_name"]}_config-check'

    # assign master id
    g['masterid'] = int(g['cluster_id']) + 1

    # variables for network and its IP for vmip function
    network_octs = g['network_ip'].split('.')
    g['network_base'] = f'{network_octs[0]}.{network_octs[1]}.{network_octs[2]}.'
    g['network_ip_prefix'] = int(network_octs[-1])

    # region optional
    g['region_string'] = ''
    if g['region']:
        g['region_string'] = g['region']

    # define vmnames
    g['suffixes'] = ['-i0', '-m1', '-m2', '-m3', '-u1', '-w1', '-w2', '-w3', '-w4', '-w5']
    g['vmnames'] = {
        g['cluster_id'] + i: f"{g['cluster_name']}{suffix}"
        for i, suffix in enumerate(g['suffixes'])
    }

    # kopsrox runs on the proxmox node itself and drives it via qm / pvesm /
    # pvesh ( no http api ) - the node name is simply this host's hostname
    g['proxmox_node'] = socket.gethostname().split('.')[0]
    proxmox_node = g['proxmox_node']

    # confirm this really is a proxmox node we can drive
    if not os.path.isdir(f'/etc/pve/nodes/{proxmox_node}'):
        kabort(g['kname'], f'this host ({proxmox_node}) does not look like a proxmox node - '
               'kopsrox must run on the pve node itself ( qm / pvesm / pvesh )')

    cluster_id = g['cluster_id']

    # discover storage + vms + status + the template config straight from the
    # pmxcfs / runtime files in ONE cheap sudo spawn ( ~0.02s ) instead of a
    # pvesh / qm call ( each ~1.4s of pve-perl startup ):
    #  - storage:      defined in /etc/pve/storage.cfg
    #  - vms on this node: /etc/pve/qemu-server/<vmid>.conf ( that dir is a symlink
    #    to the local node's, so it is exactly our guests )
    #  - running/stopped: the live pidfile /var/run/qemu-server/<vmid>.pid + a
    #    kill -0 liveness check ( the same signal qm status keys on )
    #  - the template's own .conf ( image description + disk volid ) tacked on
    disc = pve_run(['bash', '-c',
        'echo "##STORAGE"; cat /etc/pve/storage.cfg 2>/dev/null; '
        'echo "##VMS"; '
        'for f in /etc/pve/qemu-server/*.conf; do [ -e "$f" ] || continue; '
        'v=$(basename "$f" .conf); p=/var/run/qemu-server/$v.pid; '
        'if [ -f "$p" ] && kill -0 "$(cat "$p" 2>/dev/null)" 2>/dev/null; then s=running; else s=stopped; fi; '
        'echo "$v $s"; done; '
        f'echo "##TEMPLATECONF"; cat /etc/pve/qemu-server/{cluster_id}.conf 2>/dev/null; '
        f'echo "##MASTERCONF"; cat /etc/pve/qemu-server/{cluster_id + 1}.conf 2>/dev/null'],
        kname = g['kname']).stdout

    storage_block = disc.split('##STORAGE', 1)[-1].split('##VMS', 1)[0]
    vms_block = disc.split('##VMS', 1)[-1].split('##TEMPLATECONF', 1)[0]
    g['template_conf'], _, master_conf = disc.split('##TEMPLATECONF', 1)[-1].partition('##MASTERCONF')

    # storage must be defined ( catches a typo'd proxmox_storage; an inactive
    # storage surfaces later with a clear error on first use )
    storage_names = re.findall(r'^\w+:\s+(\S+)', storage_block, re.MULTILINE)
    if g['proxmox_storage'] not in storage_names:
        kabort(g['kname'], f'{g["proxmox_storage"]} storage not found in /etc/pve/storage.cfg on {proxmox_node}')

    # kopsrox vms on this node in the cluster id range, with status
    disc_vms = {}
    for line in vms_block.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        vmid = int(parts[0])
        if cluster_id <= vmid < cluster_id + 10:
            disc_vms[vmid] = {'vmid': vmid, 'name': g['vmnames'].get(vmid, str(vmid)), 'status': parts[1]}
    g['disc_vms'] = disc_vms

    # map of kopsrox vmid -> proxmox node ( always this node )
    g['vms'] = {vmid: proxmox_node for vmid in sorted(disc_vms)}
    vms = g['vms']

    # check the image exists - image create builds it so skips the check
    if not (verb == 'image' and cmd == 'create'):
        if cluster_id not in vms:
            kabort(g['kname'], f'{g["cluster_name"]} image not found - please run "kopsrox image create"')

    # image description - template may not exist yet on image create
    # the template .conf ( read above, no extra spawn ) holds the description as
    # url-encoded '#' comment lines at the top ( how pve persists it - one line
    # per description line ), plus the disk volid ( eg scsi0: local-lvm:base-500-
    # disk-0,... ) which is cached for kopsrox_img(). empty when no template yet
    cfg = g['template_conf']
    desc_lines = [unquote(line[1:]) for line in cfg.splitlines() if line.startswith('#')]
    g['cloud_image_desc'] = '\n'.join(desc_lines)
    g['cloud_image_disk'] = ''
    disk_match = re.search(rf'(\S+:\S*{cluster_id}-disk-0)', cfg)
    if disk_match:
        g['cloud_image_disk'] = disk_match.group(1)

    # config-drift baselines: the image group hash lives in the i0 description
    # ( above ), the cluster group hash in the m1 description ( parsed the same
    # way ). empty when absent - eg an image/cluster built before this existed,
    # in which case the drift notices below stay silent ( no false positives )
    master_desc = '\n'.join(unquote(line[1:]) for line in master_conf.splitlines() if line.startswith('#'))
    image_hash_match = re.search(r'config_hash: (\S+)', g['cloud_image_desc'])
    cluster_hash_match = re.search(r'config_hash: (\S+)', master_desc)
    g['image_config_hash_baked'] = image_hash_match.group(1) if image_hash_match else ''
    g['cluster_config_hash_baked'] = cluster_hash_match.group(1) if cluster_hash_match else ''

    # notify if the configured k3s_version differs from what's baked into the
    # image - image content only changes via image create, so editing the ini
    # alone does not affect a running cluster or new clones until then. skip the
    # notice on image create itself - that command is what resolves the mismatch
    image_k3s_match = re.search(r'k3s_version: (\S+)', g['cloud_image_desc'])
    if not (verb == 'image' and cmd == 'create') and image_k3s_match and image_k3s_match.group(1) != g['k3s_version']:
        kmsg(g['kname'], f'kopsrox.ini k3s_version ({g["k3s_version"]}) differs from the image '
             f'({image_k3s_match.group(1)}) - run "image create" to rebuild, then "k3s upgrade" '
             f'to apply it to the running cluster', 'sys')

    # config-drift notices for the rest ( see IMAGE_CONFIG_OPTS / CLUSTER_CONFIG_OPTS ).
    # only fire when a baseline was recorded ( else silent for old images/clusters )
    # and not during the command that resolves the drift
    if g['image_config_hash_baked'] and not (verb == 'image' and cmd == 'create') \
            and g['image_config_hash_baked'] != config_hash(IMAGE_CONFIG_OPTS):
        kmsg(g['kname'], 'kopsrox.ini image settings ( oci_image, localuser/pass/sshkey, network_dns, '
             'extra_packages, nfs_* ) changed since the image was built - run "image create" to apply', 'sys')

    if g['cluster_config_hash_baked'] and not (verb == 'cluster' and cmd in ('create', 'update', 'restore')) \
            and g['cluster_config_hash_baked'] != config_hash(CLUSTER_CONFIG_OPTS):
        kmsg(g['kname'], 'kopsrox.ini cluster settings ( s3_*, masters, workers, kubelet_args ) changed '
             'since the cluster was built - run "cluster update" to apply', 'sys')

    # guest verbs power on any stopped node
    if verb in ['cluster', 'k3s', 'etcd', 'node']:
        for vmid in vms:
            if vmid != cluster_id and disc_vms[vmid].get('status') == 'stopped':
                kmsg(g['kname'], f'powering on {disc_vms[vmid].get("name")}', 'sys')
                pve_run(['qm', 'start', str(vmid)], kname = g['kname'])

    # master reachable? agent ping only - consumed by the image bridge gate and cluster plan totals
    # an agent-alive proxy for k3s-alive: cluster_plan_total can over-count one export unit, the bar clamps
    # the ping is itself a ~1.4s qm spawn, so only run it where the result is used:
    # the cluster verb ( plan totals ) and image create ( the bridge-check gate ) -
    # image info / destroy never consult it
    g['conf_check_master_up'] = False
    masterid = g['masterid']
    if (verb == 'cluster' or (verb == 'image' and cmd == 'create')) and disc_vms.get(masterid, {}).get('status') == 'running':
        if pve_run(['qm', 'agent', str(masterid), 'ping'], check = False, kname = g['kname']).returncode == 0:
            g['conf_check_master_up'] = True

    # image related config checks - build prerequisites only, so gated to
    # image create ( pointless latency on image info / destroy: the pve-microvm
    # version gate, the upstream-release notice, and the ~1.4s pvesh bridge/sdn
    # discovery all matter only when actually building the template )
    if verb == 'image' and cmd == 'create':

        # pve-microvm version checks
        # kopsrox needs 0.3.19+ ( qm shutdown fix and the template layout we patch )
        g['microvm_ver'] = subprocess.run(['bash', '-c', "dpkg-query -W -f '${Version}' pve-microvm 2>/dev/null || echo none"], text=True, capture_output=True).stdout.strip()
        microvm_ver = g['microvm_ver']
        if microvm_ver == 'none':
            kabort(g['kname'], 'pve-microvm is not installed - see README.md')
        microvm_installed = tuple(map(int, microvm_ver.split('-')[0].split('.')))
        if microvm_installed < (0, 3, 19):
            kabort(g['kname'], f'pve-microvm {microvm_ver} is too old - kopsrox needs 0.3.19 or later')

        # notify if upstream has a newer release - skip quietly if offline
        # 0.3.20+ postinst restarts pvedaemon itself so only warn on older installs
        try:
            microvm_latest_tag = requests.get('https://api.github.com/repos/rcarmo/pve-microvm/releases/latest', timeout=3).json()['tag_name']
            if tuple(map(int, microvm_latest_tag.lstrip('v').split('.'))) > microvm_installed:
                pvedaemon_hint = ' - restart pvedaemon after upgrading!' if microvm_installed < (0, 3, 20) else ''
                kmsg(g['kname'], f'pve-microvm {microvm_latest_tag} is available ( installed: {microvm_ver} ){pvedaemon_hint}', 'sys')
        except Exception:
            pass

        # check configured bridge exists or is a sdn vnet - skipped when the cluster is already live
        network_bridge = g['network_bridge']
        if not g['conf_check_master_up']:
            if not re.search('sdn/', network_bridge):
                bridges = json.loads(pve_run(['pvesh', 'get', f'/nodes/{proxmox_node}/network',
                                              '--type', 'bridge', '--output-format', 'json'], kname = g['kname']).stdout)
                discovered_bridges = [b.get('iface') for b in bridges]
            else:
                # check we can map zone and get vnets
                try:
                    sdn_params = network_bridge.split('/')
                    zone = sdn_params[1]
                    network_bridge = sdn_params[2]
                except Exception:
                    kabort(g['kname'], f'unable to parse sdn config: "{network_bridge}"')

                # discover available sdn bridges
                content = json.loads(pve_run(['pvesh', 'get', f'/nodes/{proxmox_node}/sdn/zones/{zone}/content',
                                              '--output-format', 'json'], kname = g['kname']).stdout)
                discovered_bridges = [b.get('vnet') for b in content]

            # check configured bridge is in list
            if network_bridge not in discovered_bridges:
                kabort(g['kname'], f'"{network_bridge}" not found. valid bridges: {discovered_bridges}')
            g['network_bridge'] = network_bridge
