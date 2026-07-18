#!/usr/bin/env python3

# generate the default kopsrox.ini
def init_kopsrox_ini():

  # import config parser
  from configparser import ConfigParser

  # get config
  config = ConfigParser(allow_no_value=True)
  config.read('kopsrox.ini')
  ks = 'kopsrox'

  # proxmox section
  config.add_section('kopsrox')

  # proxmox api endpoint
  config.set(ks, '; domain or IP to access proxmox')
  config.set(ks, 'proxmox_endpoint', '127.0.0.1')

  # proxmox api port
  config.set(ks, '; api port ( usually 8006 ) ')
  config.set(ks, 'proxmox_api_port', '8006')

  # username
  config.set(ks, '; username to connect with / owner of the API token')
  config.set(ks, 'proxmox_user', 'root@pam')

  # api token name
  config.set(ks, '; name of api token')
  config.set(ks, 'proxmox_token_name', 'kopsrox')

  # api key
  config.set(ks, '; text of api key')
  config.set(ks, 'proxmox_token_value', 'xxxxxxxxxxxxx')

  # node to operate on
  config.set(ks, '; the proxmox node that you will run kopsrox on - the image and all nodes are created on this host')
  config.set(ks, 'proxmox_node', 'proxmox')

  # storage on node
  config.set(ks, '; the proxmox storage to use for kopsrox - needs to be available on the proxmox node')
  config.set(ks, 'proxmox_storage', 'local-lvm')

  # upstream image
  config.set(ks, '; the OCI image used to build the microvm template ( via pve-microvm-template )')
  config.set(ks, 'oci_image', 'ubuntu:24.04')

  # microvm kernel/initrd - optional overrides
  config.set(ks, '; kernel/initrd used to boot kopsrox microvms - built with dev/build-kopsrox-kernel.sh')
  config.set(ks, '# microvm_kernel', '/usr/share/pve-microvm/vmlinuz-kopsrox')
  config.set(ks, '# microvm_initrd', '/usr/share/pve-microvm/initrd-kopsrox')

  # extra packages to include in image
  config.set(ks, '; comma seperated list of extra packages installed into each node when created ')
  config.set(ks, 'extra_packages', 'nfs-common')

  # disk size for kopsrox vms
  config.set(ks, '; size of vm disk in Gib ')
  config.set(ks, 'vm_disk', '20')

  # number of cpu cores
  config.set(ks, '; number of cpu cores ')
  config.set(ks, 'vm_cpu', '1')

  # ram size
  config.set(ks, '; amount of ram in Gib ')
  config.set(ks, 'vm_ram', '2')

  # node user key and password
  config.set(ks, '; username for the user created in each node ( via the guest agent )')
  config.set(ks, 'cloudinituser', 'user')

  # node user password
  config.set(ks, '; password for the created user')
  config.set(ks, 'cloudinitpass', 'admin')

  # node user ssh key
  config.set(ks, '; ssh public key for the created user ( required )')
  config.set(ks, 'cloudinitsshkey', 'ssh-rsa cioieocieo')

  # network bridge
  config.set(ks, '; network bridge to use with kopsrox')
  config.set(ks, '; a proxmox sdn can be used by specifying the zone and vnet like this: sdn/zone/vnet')
  config.set(ks, 'network_bridge', 'vmbr0')

  # kopsrox network baseip
  config.set(ks, '; first ip of the ip range used for this kopsrox cluster')
  config.set(ks, 'network_ip', '192.168.0.160')

  # netmask / subnet
  config.set(ks, '; /24 is 255.255.255.0')
  config.set(ks, 'network_mask', '24')

  # default gateway
  config.set(ks, '; default gateway for the network ( needs to provide internet access ) ')
  config.set(ks, 'network_gw', '192.168.0.1')

  # dns server
  config.set(ks, '; dns server for network')
  config.set(ks, 'network_dns', '192.168.0.1')

  # network mtu
  config.set(ks, '; interface mtu applied inside each node ')
  config.set(ks, '; set to 1450 if using sdn ')
  config.set(ks, 'network_mtu', '1500')

  # cluster vmid
  config.set(ks, '; id for the cluster vm\'s eg from 620 - 630')
  config.set(ks, 'cluster_id', '620')

  # cluster friendly name
  config.set(ks, '; name of the cluster')
  config.set(ks, 'cluster_name', 'mycluster')

  # number of masters
  config.set(ks, '; number of masters nodes 1 or 3')
  config.set(ks, 'masters', '1')

  # number of workers
  config.set(ks, '; number of workers nodes 1 to 5')
  config.set(ks, 'workers', '1')

  # k3s version
  config.set(ks, '; k3s version')
  config.set(ks, 'k3s_version', 'v1.34.5+k3s1')

  # s3 endpoint
  config.set(ks, '; s3 endpoint')
  config.set(ks, 's3_endpoint', 'kopsrox')

  # s3 region
  config.set(ks, '; s3 region - leave as \'\' for no region')
  config.set(ks, '# s3_region', '\'\'')

  # s3 access key
  config.set(ks, '; s3 access key')
  config.set(ks, 's3_access-key', 'e3898d39d39id93')

  # s3 access secret
  config.set(ks, '; s3 access secret')
  config.set(ks, 's3_access-secret', 'ioewioeiowe')

  # s3 bucket
  config.set(ks, '; s3 bucket')
  config.set(ks, 's3_bucket', 'kopsrox-backup')

  # write config
  # file should not already exist...
  with open('kopsrox.ini', 'w') as cfile:
    config.write(cfile)
  print('created kopsrox.ini please edit for your setup')
  return
