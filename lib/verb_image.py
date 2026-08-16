#!/usr/bin/env python3

import os
import re
from datetime import datetime

import requests

from kopsrox_artifacts import k3s_config, k3s_registries, kopsrox_manifest
from kopsrox_config import (
    cloud_image_desc,
    cluster_id,
    cluster_name,
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
    prox,
    proxmox_node,
    proxmox_storage,
    vm_cpu,
    vm_ram,
)
from kopsrox_kmsg import kabort, kmsg, kplan, kplan_tick, kstep
from kopsrox_proxmox import prox_destroy, prox_task, qa_exec, qa_write


# generate a patched copy of pve-microvm-template
# - the ubuntu oci image ships an empty /etc/resolv.conf so the chroot package
#   install fails silently and the template ends up without systemd/qemu-ga
# - the first boot installer blocks multi-user.target ( and so the guest agent )
#   waiting for a network kopsrox has not configured yet
def patch_microvm_template() -> str:
    kname = 'image_'

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
        # udev - without it dev-ttyS0.device never appears and the console= generated
        # serial-getty stalls boot for its full 90s device timeout
        # sudo - saves an apt-get run on every node create
        # systemd-timesyncd - no ntp in the oci image
        # ( dbus is upstream since pve-microvm 0.3.19 )
        # drop isc-dhcp-client - unused on the apt path ( networkd's built-in
        # dhcp client serves the fresh clone's initial lease, then node_prepare
        # switches to static; upstream's dhclient fallback is alpine/fedora only )
        ('PKGS="iproute2 isc-dhcp-client systemd systemd-sysv ca-certificates curl dbus"',
         'PKGS="iproute2 systemd systemd-sysv ca-certificates curl dbus udev sudo systemd-timesyncd"'),
        # with udev installed serial-getty would start and fight microvm-console for ttyS0
        ('systemctl enable serial-getty@ttyS0.service 2>/dev/null || true',
         'systemctl mask serial-getty@ttyS0.service 2>/dev/null || true'),
        # defer template conversion - a proxmox template can never be started
        # again, but image_create() needs to boot this vm once via the guest
        # agent to bake k3s in first; it converts to a template itself once done
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

    kmsg(f'{kname}create', f'{cluster_name}-i0 microvm template based on {oci_image}', 'sys')

    # template build / rootfs verify / kernel args / bake k3s / tag
    kplan(5, f'{cluster_name} image create')

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

    # inspection copies of the manifest and a sample master config.yaml -
    # actual per-node config.yaml is generated role-aware at join time
    # ( kopsrox_k3s.k3s_join ), this is just for local review
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
    with kstep(f'{kname}template', f'running {microvm_template} ( log: kopsrox-image.log )'):
        local_exec(f'sudo bash {microvm_template} --image {oci_image} --vmid {cluster_id} \
--name {cluster_name}-i0 --storage {proxmox_storage} --disk-size 4G --memory {vm_ram * 1024} \
--cores {vm_cpu} --profile standard --no-docker > kopsrox-image.log 2>&1')
    kplan_tick()

    # the template build hides chroot failures - verify the guest actually has
    # systemd and the guest agent before going any further. disk is still
    # "vm-" ( not "base-" ) here - template conversion is deferred until after
    # the k3s bake step below
    with kstep(f'{kname}verify', 'checking template rootfs'):
        img_dev = local_exec(f'sudo pvesm path {proxmox_storage}:vm-{cluster_id}-disk-0').stdout.strip()
        img_check = local_exec(f'''
sudo lvchange -ay -K {img_dev} 2>/dev/null || true
sudo mkdir -p /tmp/kopsrox-img-check
if sudo mount -o ro {img_dev} /tmp/kopsrox-img-check 2>/dev/null; then
  ls /tmp/kopsrox-img-check/usr/lib/systemd/systemd /tmp/kopsrox-img-check/usr/sbin/qemu-ga > /dev/null 2>&1 && echo ok || echo missing
  sudo umount /tmp/kopsrox-img-check
  sudo lvchange -an {img_dev} 2>/dev/null || true
else
  echo nomount
fi''').stdout.strip()
    kplan_tick()
    if re.search('missing', img_check):
        kabort(f'{kname}check', 'template rootfs is missing systemd/qemu-ga - check kopsrox-image.log')
    if re.search('nomount', img_check):
        kmsg(f'{kname}check', 'unable to mount template disk to verify rootfs - continuing', 'sys')

    # boot template with the kopsrox kernel - args is root only so use qm not the api
    kmsg(f'{kname}kernel', microvm_kernel)
    local_exec(f'sudo qm set {cluster_id} --args \'-kernel {microvm_kernel} \
-initrd {microvm_initrd} -append "rdinit=/init console=ttyS0 root=/dev/vda rw ipv6.disable=1 net.ifnames=0"\'')
    kplan_tick()

    # boot the template once to bake k3s and every cluster-wide ( not
    # per-node ) piece of node_prepare() into it via the guest agent - the
    # local user, DNS, extra_packages and the kube-vip/traefik manifest are
    # identical for every node in this cluster, so there is no reason to
    # redo them on each clone. only genuinely per-node state ( static ip,
    # hostname, machine-id, root fs resize ) still happens in node_prepare()
    with kstep(f'{kname}k3s', f'baking k3s {k3s_version} into the template') as step:
        prox_task(prox.nodes(proxmox_node).qemu(cluster_id).status.start.post())
        qa_write(cluster_id, '/root/k3s-install.sh', open(get_k3s_path).read(), '755')
        install_env = f'INSTALL_K3S_VERSION={k3s_version} INSTALL_K3S_SKIP_START=true INSTALL_K3S_SKIP_ENABLE=true'
        qa_exec(cluster_id, f'{install_env} /root/k3s-install.sh server > /k3s_install_server.log 2>&1')
        qa_exec(cluster_id, f'{install_env} /root/k3s-install.sh agent > /k3s_install_agent.log 2>&1')
        qa_exec(cluster_id, 'rm -f /root/k3s-install.sh')
        baked_check = qa_exec(cluster_id, 'test -x /usr/local/bin/k3s '
                               '&& test -f /etc/systemd/system/k3s.service '
                               '&& test -f /etc/systemd/system/k3s-agent.service '
                               '&& echo ok || echo missing')
        if baked_check != 'ok':
            kabort(f'{kname}k3s', 'k3s binary or systemd units missing after install - check /k3s_install_*.log on the template')
        qa_exec(cluster_id, 'rm -f /k3s_install_server.log /k3s_install_agent.log')

        step.msg = 'baking cluster-wide config into the template'
        qa_exec(cluster_id, f'useradd -m -s /bin/bash -G sudo {localuser} 2>/dev/null; echo {localuser}:{localpass} | chpasswd')
        qa_exec(cluster_id, f'mkdir -p /home/{localuser}/.ssh /etc/sudoers.d')
        qa_write(cluster_id, f'/home/{localuser}/.ssh/authorized_keys', f'{localsshkey}\n', '600')
        qa_exec(cluster_id, f'chown -R {localuser}:{localuser} /home/{localuser}/.ssh')
        qa_write(cluster_id, f'/etc/sudoers.d/{localuser}', f'{localuser} ALL=(ALL) NOPASSWD:ALL\n', '440')
        qa_write(cluster_id, '/etc/resolv.conf', f'nameserver {network_dns}\n')
        # the nfs storageclass mounts via kubelet on the host, which needs the
        # /sbin/mount.nfs helper from nfs-common - the built-in kernel nfs client
        # is not enough. pull it in whenever nfs_server is set, regardless of
        # what extra_packages says ( it may be blank or omit nfs-common )
        packages = [p for p in extra_packages.replace(',', ' ').split() if p]
        # always-on packages, regardless of extra_packages:
        # - vim-tiny: a usable editor for debugging a node over the console/ssh
        # - unzip: lets 'cluster restore' handle a legacy compressed ( .zip ) etcd
        #   snapshot: k3s <=1.34 doubles the path decompressing one itself, so
        #   kopsrox_k3s restore unzips it and restores the plain file ( new
        #   snapshots are uncompressed - etcd-snapshot-compress is off )
        for pkg in ('vim-tiny', 'unzip'):
            if pkg not in packages:
                packages.append(pkg)
        if nfs_server != '' and 'nfs-common' not in packages:
            packages.append('nfs-common')
        if packages:
            qa_exec(cluster_id, f'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq 2>/dev/null && apt-get install -y -qq {" ".join(packages)} 2>/dev/null')
        # /etc/rancher/k3s holds each node's config.yaml, written at join time -
        # bake the dir in so k3s_join() only has to push the file ( the agent
        # file-write api does not create parent dirs )
        qa_exec(cluster_id, 'mkdir -p /etc/rancher/k3s /var/lib/rancher/k3s/server/manifests')
        qa_write(cluster_id, f'/var/lib/rancher/k3s/server/manifests/kopsrox-{cluster_name}.yaml', kopsrox_manifest())
        # embedded-registry: true in config.yaml starts the Spegel P2P mesh, but
        # k3s only mirrors registries listed under mirrors: here - bake the
        # wildcard registries.yaml in so every node ( server + agent ) has it
        qa_write(cluster_id, '/etc/rancher/k3s/registries.yaml', k3s_registries())

        # graceful agent-driven shutdown ( pve-microvm >= 0.3.19, already relied
        # on elsewhere - see CLAUDE.md ) so the just-written files are flushed,
        # then convert to a proxmox template ourselves - patch_microvm_template()
        # deferred this so the vm could still be started for the steps above
        prox_task(prox.nodes(proxmox_node).qemu(cluster_id).status.shutdown.post())
        local_exec(f'sudo qm template {cluster_id}')
    kplan_tick()

    # define image desc
    img_ts = str(datetime.now())
    image_desc = f'''
cluster_name: {cluster_name}
oci_image: {oci_image}
k3s_version: {k3s_version}
created: {img_ts}'''

    # tag and describe the template
    prox_task(prox.nodes(proxmox_node).qemu(cluster_id).config.post(
        description=image_desc,
        tags=f'{cluster_name},microvm',
    ))
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
