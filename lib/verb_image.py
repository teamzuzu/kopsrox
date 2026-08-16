#!/usr/bin/env python3

import os
import re
from datetime import datetime

import requests

from kopsrox_artifacts import k3s_config, k3s_registries, kopsrox_manifest
from kopsrox_config import (
    IMAGE_CONFIG_OPTS,
    cloud_image_desc,
    cluster_id,
    cluster_name,
    config_hash,
    extra_packages,
    image_info,
    k3s_version,
    kopsrox_img,
    local_exec,
    localpass,
    localsshkey,
    localuser,
    microvm_initrd,
    microvm_kernel,
    network_dns,
    nfs_server,
    oci_image,
    proxmox_storage,
    pve_run,
    vm_cpu,
    vm_ram,
)
from kopsrox_kmsg import kabort, kmsg, kplan, kplan_tick, kstep
from kopsrox_proxmox import prox_destroy, qa_exec, qa_write


# generate a patched copy of pve-microvm-template
# - the ubuntu oci image ships an empty /etc/resolv.conf so the chroot package
#   install fails silently and the template ends up without systemd/qemu-ga
# - the first boot installer blocks multi-user.target ( and so the guest agent )
#   waiting for a network kopsrox has not configured yet
def patch_microvm_template() -> str:
    kname = 'image_'

    # extra packages folded into the single chroot install below rather than a
    # second live apt run after boot - one apt transaction, no post-boot network
    # dependency for packages:
    # - vim-tiny: a usable editor for debugging a node over the console/ssh
    # - unzip: lets 'cluster restore' handle a legacy compressed ( .zip ) etcd
    #   snapshot ( k3s <=1.34 doubles the path decompressing one itself, so
    #   kopsrox_k3s restore unzips it and restores the plain file )
    # - nfs-common ( when nfs_server is set ): the 'nfs' storageclass mounts via
    #   kubelet on the host, which needs the /sbin/mount.nfs helper - that binary
    #   ships in nfs-common itself, so --no-install-recommends still provides it
    #   ( only rpcbind, an nfsv3-only recommend, is skipped; nfsv4 is unaffected )
    packages = [p for p in extra_packages.replace(',', ' ').split() if p]
    for pkg in ('vim-tiny', 'unzip'):
        if pkg not in packages:
            packages.append(pkg)
    if nfs_server != '' and 'nfs-common' not in packages:
        packages.append('nfs-common')
    extra_pkgs = (' ' + ' '.join(packages)) if packages else ''

    # patches as ( old, new ) - each must match or upstream changed and we bail
    patches = [
        # always write dns for the chroot - the guard misses empty files -
        # and use the configured network_dns, not upstream's hardcoded 1.1.1.1
        ('[ -f "$ROOTFS_DIR/etc/resolv.conf" ] || echo "nameserver 1.1.1.1"',
         f'echo "nameserver {network_dns}"'),
        # surface apt errors into the build log
        ('apt-get update -qq 2>/dev/null',
         'apt-get update -qq'),
        ('apt-get install -y -qq --no-install-recommends $PKGS 2>/dev/null',
         'apt-get install -y -qq --no-install-recommends $PKGS'),
        # do not enable the first boot installer
        ('''        ln -sf ../microvm-setup.service \\
            "$ROOTFS_DIR/etc/systemd/system/multi-user.target.wants/microvm-setup.service"''',
         '        : # kopsrox: microvm-setup not enabled'),

        # drop isc-dhcp-client - unused 
        ('PKGS="iproute2 isc-dhcp-client systemd systemd-sysv ca-certificates curl dbus"',
         f'PKGS="iproute2 systemd systemd-sysv ca-certificates curl dbus udev sudo systemd-timesyncd{extra_pkgs}"'),
        # with udev installed serial-getty would start and fight microvm-console for ttyS0
        ('systemctl enable serial-getty@ttyS0.service 2>/dev/null || true',
         'systemctl mask serial-getty@ttyS0.service 2>/dev/null || true'),
        # don't create image at this stage
        ('log "Converting to template..."\nqm template "$TEMPLATE_VMID"',
         'log "Converting to template..."\n: # kopsrox: template conversion deferred to image_create()'),
    ]

    template_script = open('/usr/bin/pve-microvm-template').read()
    for old, new in patches:
        if old not in template_script:
            kabort(f'{kname}patch', f'pve-microvm-template patch failed - upstream changed?\n{old}')
        template_script = template_script.replace(old, new)

    patched_path = './lib/scripts/microvm-template.sh'
    open(patched_path, 'w').write(template_script)
    os.chmod(patched_path, 0o755)
    return patched_path


# create image ( rebuilds from scratch if one already exists )
def image_create() -> None:
    kname = 'image_'

    # template build / rootfs verify / kernel args / bake k3s / tag
    kplan(5, f'creating {cluster_name}-i0 / {oci_image}')

    # check pve-microvm is installed on this node
    if not os.path.isfile('/usr/share/pve-microvm/vmlinuz'):
        kabort(f'{kname}check', 'pve-microvm not installed - see https://github.com/rcarmo/pve-microvm')

    # check the kopsrox kernel has been built
    if not (os.path.isfile(microvm_kernel) and os.path.isfile(microvm_initrd)):
        kabort(f'{kname}check', f'{microvm_kernel} not found - run dev/build-kopsrox-kernel.sh')

    # download k3s.sh
    get_k3s_path = './lib/scripts/k3s.sh'
    if not os.path.isfile(get_k3s_path):
        kmsg(f'{kname}get-k3s', f'downloading script from https://get.k3s.io...')
        try:
            dl_k3s = requests.get('https://get.k3s.io')
            open(get_k3s_path, 'wb').write(dl_k3s.content)
        except Exception:
            kabort(f'{kname}check', f'unable to download get k3s script')
  
    open(f'./lib/manifests/kopsrox-{cluster_name}.yaml', 'w').write(kopsrox_manifest())
    open('./lib/manifests/config.yaml', 'w').write(k3s_config('master'))
    open('./lib/manifests/registries.yaml', 'w').write(k3s_registries())

    # destroy template if it exists
    try:
        prox_destroy(cluster_id)
    except Exception:
        pass

    # build the microvm template with a patched copy of pve-microvm-template
    microvm_template = patch_microvm_template()
    with kstep(f'{kname}template', f'running {microvm_template}'):
        local_exec(f'sudo bash {microvm_template} --image {oci_image} --vmid {cluster_id} \
--name {cluster_name}-i0 --storage {proxmox_storage} --disk-size 4G --memory {vm_ram * 1024} \
--cores {vm_cpu} --profile standard --no-docker > kopsrox-image.log 2>&1')
    kplan_tick()

    # boot template with the kopsrox kernel - args is root only so use qm not the api
    local_exec(f'sudo qm set {cluster_id} --args \'-kernel {microvm_kernel} \
-initrd {microvm_initrd} -append "rdinit=/init console=ttyS0 root=/dev/vda rw ipv6.disable=1 net.ifnames=0"\'')
    kplan_tick()

    # add k3s
    with kstep(f'{kname}k3s', f'installing {k3s_version}') as step:
        pve_run(['qm', 'start', str(cluster_id)])
        qa_write(cluster_id, '/root/k3s-install.sh', open(get_k3s_path).read(), '755')
        # both installer runs in one exec ( server unit shared by master/slave,
        # separate agent unit for workers ) plus the script cleanup - each
        # qa_exec is a fixed ~0.5-1s round trip, so batch what has no dependency.
        # each run redirects to its own log so nothing leaks to stderr ( which
        # qa_exec would treat as a failure )
        install_env = f'INSTALL_K3S_VERSION={k3s_version} INSTALL_K3S_SKIP_START=true INSTALL_K3S_SKIP_ENABLE=true'
        qa_exec(cluster_id, f'{install_env} /root/k3s-install.sh server > /k3s_install_server.log 2>&1; '
                            f'{install_env} /root/k3s-install.sh agent > /k3s_install_agent.log 2>&1; '
                            'rm -f /root/k3s-install.sh')
        baked_check = qa_exec(cluster_id, 'test -x /usr/local/bin/k3s '
                               '&& test -f /etc/systemd/system/k3s.service '
                               '&& test -f /etc/systemd/system/k3s-agent.service '
                               '&& echo ok || echo missing')
        if baked_check != 'ok':
            kabort(f'{kname}k3s', 'k3s binary or systemd units missing after install - check /k3s_install_*.log on the template')

        step.msg = f'generating local config...'

        # one exec for all the account setup + directory creation 
        qa_exec(cluster_id,
                f'rm -f /k3s_install_server.log /k3s_install_agent.log; '
                f'useradd -m -s /bin/bash -G sudo {localuser} 2>/dev/null; '
                f'echo {localuser}:{localpass} | chpasswd; '
                f'mkdir -p /home/{localuser}/.ssh /etc/sudoers.d /etc/rancher/k3s /var/lib/rancher/k3s/server/manifests')
        qa_write(cluster_id, f'/home/{localuser}/.ssh/authorized_keys', f'{localsshkey}\n', '600')
        qa_write(cluster_id, f'/etc/sudoers.d/{localuser}', f'{localuser} ALL=(ALL) NOPASSWD:ALL\n', '440')
        qa_write(cluster_id, '/etc/resolv.conf', f'nameserver {network_dns}\n')
        qa_write(cluster_id, f'/var/lib/rancher/k3s/server/manifests/kopsrox-{cluster_name}.yaml', kopsrox_manifest())
        # embedded-registry: true in config.yaml starts the Spegel P2P mesh, but
        # k3s only mirrors registries listed under mirrors: here - bake the
        # wildcard registries.yaml in so every node ( server + agent ) has it
        qa_write(cluster_id, '/etc/rancher/k3s/registries.yaml', k3s_registries())
        # the writes above run as root - fix ownership of the ssh dir it created
        qa_exec(cluster_id, f'chown -R {localuser}:{localuser} /home/{localuser}/.ssh')

        # graceful agent-driven shutdown 
        pve_run(['qm', 'shutdown', str(cluster_id)])
        local_exec(f'sudo qm template {cluster_id}')
    kplan_tick()

    # define image desc
    img_ts = str(datetime.now())
    image_desc = f'''
cluster_name: {cluster_name}
oci_image: {oci_image}
k3s_version: {k3s_version}
created: {img_ts}
config_hash: {config_hash(IMAGE_CONFIG_OPTS)}'''

    # tag and describe the template
    pve_run(['qm', 'set', str(cluster_id), '--description', image_desc, '--tags', f'{cluster_name},microvm'])
    kplan_tick()

# destroy image
def image_destroy() -> None:
    kname = 'image_'
    kmsg(f'{kname}destroy', f'{kopsrox_img()}/{cloud_image_desc}', 'sys')
    prox_destroy(cluster_id)


def run(cmd: str, arg: str | None = None) -> None:

    # create image ( rebuilds from scratch if one already exists )
    if cmd == 'create':
        image_create()

    # image info
    if cmd == 'info':
        image_info()

    # destroy image
    if cmd == 'destroy':
        image_destroy()
