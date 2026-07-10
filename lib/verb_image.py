#!/usr/bin/env python3

# functions
from kopsrox_config import *
from kopsrox_proxmox import prox_task, prox_destroy
from kopsrox_artifacts import kopsrox_manifest, k3s_server_config, kopsrox_sh

# define command
cmd = sys.argv[2]
kname = 'image_'

# generate a patched copy of pve-microvm-template
# - the ubuntu oci image ships an empty /etc/resolv.conf so the chroot package
#   install fails silently and the template ends up without systemd/qemu-ga
# - the first boot installer blocks multi-user.target ( and so the guest agent )
#   waiting for a network kopsrox has not configured yet
def patch_microvm_template():

  # patches as ( old, new ) - each must match or upstream changed and we bail
  patches = [
    # always write dns for the chroot - the guard misses empty files
    ('[ -f "$ROOTFS_DIR/etc/resolv.conf" ] || echo "nameserver 1.1.1.1"',
     'echo "nameserver 1.1.1.1"'),
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
    ('PKGS="iproute2 isc-dhcp-client systemd systemd-sysv ca-certificates curl dbus"',
     'PKGS="iproute2 isc-dhcp-client systemd systemd-sysv ca-certificates curl dbus udev sudo systemd-timesyncd"'),
    # with udev installed serial-getty would start and fight microvm-console for ttyS0
    ('systemctl enable serial-getty@ttyS0.service 2>/dev/null || true',
     'systemctl mask serial-getty@ttyS0.service 2>/dev/null || true'),
  ]

  template_script = open('/usr/bin/pve-microvm-template').read()
  for old, new in patches:
    if old not in template_script:
      kmsg(f'{kname}patch', f'pve-microvm-template patch failed - upstream changed?\n{old}', 'err')
      exit(0)
    template_script = template_script.replace(old, new)

  patched_path = './lib/scripts/microvm-template.sh'
  open(patched_path, 'w').write(template_script)
  os.chmod(patched_path, 0o755)
  return(patched_path)

# create image
if cmd == 'create':

  kmsg(f'{kname}create', f'{cluster_name}-i0 microvm template based on {oci_image}', 'sys')

  # check pve-microvm is installed on this node
  if not os.path.isfile('/usr/share/pve-microvm/vmlinuz'):
    kmsg(f'{kname}check', 'pve-microvm not installed - see https://github.com/rcarmo/pve-microvm', 'err')
    exit(0)

  # check the kopsrox kernel has been built
  if not (os.path.isfile(microvm_kernel) and os.path.isfile(microvm_initrd)):
    kmsg(f'{kname}check', f'{microvm_kernel} not found - run dev/build-kopsrox-kernel.sh', 'err')
    exit(0)

  # download k3s.sh
  get_k3s_path = './lib/scripts/k3s.sh'
  if not os.path.isfile(get_k3s_path):
    kmsg(f'{kname}get-k3s', f'downloading script from https://get.k3s.io...')
    try:
      dl_k3s = requests.get('https://get.k3s.io')
      open(get_k3s_path, 'wb').write(dl_k3s.content)
    except:
      kmsg(f'{kname}check', f'unable to download get k3s script', 'err')
      exit(1)

  # generate cluster artifacts - pushed into nodes at create time via the guest agent
  open(f'./lib/manifests/kopsrox-{cluster_name}.yaml', 'w').write(kopsrox_manifest())
  open('./lib/manifests/config.yaml', 'w').write(k3s_server_config())
  open('./lib/scripts/kopsrox.sh', 'w').write(kopsrox_sh())
  os.chmod('./lib/scripts/kopsrox.sh', 0o755)

  # destroy template if it exists
  try:
    prox_destroy(cluster_id)
  except:
    pass

  # build the microvm template with a patched copy of pve-microvm-template
  microvm_template = patch_microvm_template()
  kmsg(f'{kname}template', f'running {microvm_template} ( log: kopsrox-image.log )')
  local_exec(f'sudo bash {microvm_template} --image {oci_image} --vmid {cluster_id} \
--name {cluster_name}-i0 --storage {proxmox_storage} --disk-size 2G --memory 1024 \
--cores 1 --profile standard --no-docker > kopsrox-image.log 2>&1')

  # the template build hides chroot failures - verify the guest actually has
  # systemd and the guest agent before going any further
  img_dev = local_exec(f'sudo pvesm path {proxmox_storage}:base-{cluster_id}-disk-0').stdout.strip()
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
  if re.search('missing', img_check):
    kmsg(f'{kname}check', 'template rootfs is missing systemd/qemu-ga - check kopsrox-image.log', 'err')
    exit(0)
  if re.search('nomount', img_check):
    kmsg(f'{kname}check', 'unable to mount template disk to verify rootfs - continuing', 'sys')

  # boot template with the kopsrox kernel - args is root only so use qm not the api
  kmsg(f'{kname}kernel', microvm_kernel)
  local_exec(f'sudo qm set {cluster_id} --args \'-kernel {microvm_kernel} \
-initrd {microvm_initrd} -append "rdinit=/init console=ttyS0 root=/dev/vda rw ipv6.disable=1 net.ifnames=0"\'')

  # define image desc
  img_ts = str(datetime.now())
  image_desc = f'''
cluster_name: {cluster_name}
oci_image: {oci_image}
k3s_version: {k3s_version}
created: {img_ts}'''

  # tag and describe the template
  prox_task(prox.nodes(proxmox_node).qemu(cluster_id).config.post(
    description = image_desc,
    tags = f'{cluster_name},microvm',
  ))

# image info
if cmd == 'info':
  image_info()

# destroy image
if cmd == 'destroy':
   kmsg(f'{kname}destroy', f'{kopsrox_img()}/{cloud_image_desc}', 'sys')
   prox_destroy(cluster_id)
