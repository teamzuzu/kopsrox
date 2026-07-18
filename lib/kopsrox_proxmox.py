#!/usr/bin/env python3

# kopsrox
from kopsrox_config import *
from kopsrox_artifacts import kopsrox_manifest, k3s_server_config, kopsrox_sh

# run a exec via qemu-agent
def qa_exec(vmid: int = masterid,cmd = 'uptime', node: str = proxmox_node):

  # define kname
  kname = 'proxmox_qa_exec'

  # get vmname and node
  vmname = vmnames[vmid]
  try:
    node = vms[vmid]
  except:
    pass

  # qagent no yet running check
  qagent_running = 'false'

  # max wait time
  qagent_count = int(1)

  # while variable is false
  while qagent_running == 'false':
    try:

      # qa ping the vm
      qa_ping = prox.nodes(proxmox_node).qemu(vmid).agent.ping.post()

      # agent is running
      qagent_running = 'true'

    # agent not running
    except:
      # increment counter
      qagent_count += 1

      # exit if longer than 120 seconds - agent can be slow on first boot
      if qagent_count == 120:
        kmsg(kname, f'agent not responding on {vmname} [{node}] cmd: {cmd}', 'err')
        exit(0)

      # sleep 1 second then try again
      time.sleep(1)

      if qagent_count == 10:
        kmsg(kname, f'no response for 10s {vmname} [{node}] cmd: {cmd}', 'sys')

  # send command
  try:
    qa_exec = prox.nodes(node).qemu(vmid).agent.exec.post(
            command = "bash -c \'" + cmd +"\'",
            )
  except:
    kmsg(kname, f'problem running cmd: {cmd}', 'err')
    print(qa_exec)
    exit(0)

  # get pid
  pid = qa_exec['pid']
  pid_status = int(0)

  # loop until command has finish
  # fixme needs a loop counter?
  while pid_status != int(1):
    try:
      pid_check = prox.nodes(proxmox_node).qemu(vmid).agent('exec-status').get(pid = pid)
    except:
      kmsg(kname, f'problem with pid: {pid} {cmd}', 'err')
      exit(0)

    # returns 1 when process is done
    pid_status = pid_check['exited']
    time.sleep(0.5)

  # check for exitcode 127
  if int(pid_check['exitcode']) == 127:
    kmsg(kname, f'exit code 127: {pid} {cmd}', 'err')
    exit(0)

  # check for err-data
  try:

    # if stderr / err-data exists
    if (pid_check['err-data']):

      # print data warning \
      kmsg('qa_exec_stderr', (cmd + '\n' + pid_check['err-data'].strip()), 'err')

      # if there is output return that otherwise exit
      if (pid_check['err-data'] and pid_check['out-data']):
        return(pid_check['out-data'].strip())
      else:
        exit(0)

  except:
    try:
      # this is where data gets returned for an OK command
      if (pid_check['out-data']):
        # return it minus any line break
        return(pid_check['out-data'].strip())
    except:
      return('no output-' + cmd)

# write a file into a vm via the guest agent
def qa_write(vmid: int, remote_path: str, content: str, mode: str = '644'):

  # define kname
  kname = 'proxmox_qa_write'

  # get node for vm - fallback to configured node for new clones
  try:
    node = vms[vmid]
  except:
    node = proxmox_node

  # pve api file-write content limit
  chunk_size = 40960

  try:

    # single write
    if len(content) <= chunk_size:
      prox.nodes(node).qemu(vmid).agent('file-write').post(file = remote_path, content = content)

    # write chunks as part files then join
    else:
      chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
      for count, chunk in enumerate(chunks):
        prox.nodes(node).qemu(vmid).agent('file-write').post(file = f'{remote_path}.kopsrox{count:03}', content = chunk)
      qa_exec(vmid, f'cat {remote_path}.kopsrox* > {remote_path} && rm -f {remote_path}.kopsrox*')

  except:
    kmsg(kname, f'unable to write {remote_path} to {vmnames[vmid]}', 'err')
    exit(0)

  # set permissions
  qa_exec(vmid, f'chmod {mode} {remote_path}')

# reboot a node via the agent and wait for it to return
def node_reboot_wait(vmid: int):

  # define kname
  kname = 'proxmox_reboot'
  vmname = vmnames[vmid]
  kmsg(kname, f'rebooting {vmname}')

  # note the current boot id - microvms reboot in about a second so watching
  # for the agent to go down is a race we can lose
  boot_id = qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id')

  # transient timer so the exec returns before the agent goes away
  qa_exec(vmid, 'systemd-run --on-active=1 systemctl reboot 2>/dev/null')

  # wait for a new boot id
  count = int(0)
  while True:
    time.sleep(2)
    try:
      if qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id') != boot_id:
        break
    except:
      pass
    count += 1
    if count == 60:
      kmsg(kname, f'{vmname} did not reboot', 'err')
      exit(0)

# configure a newly cloned microvm via the guest agent
# the agent runs over virtio-serial so this works before networking is up
def node_prepare(vmid: int):

  # define kname
  kname = 'proxmox_node-prepare'
  vmname = vmnames[vmid]

  # skip already prepared nodes
  if qa_exec(vmid, 'test -f /etc/kopsrox-node-init-done && echo done || echo todo') == 'done':
    internet_check(vmid)
    return

  kmsg(kname, f'configuring {vmname}')

  # neutralise pve-microvm first boot services
  # microvm-setup waits for network then installs cloud-init/docker
  # microvm-static-net regenerates network config every boot
  qa_exec(vmid, 'systemctl disable --now microvm-setup.service 2>/dev/null; touch /etc/microvm-setup-done; systemctl disable microvm-static-net.service 2>/dev/null; true')

  # static network via systemd-networkd
  # match on driver - Type=ether also matches dummy0/sit0 from the kopsrox kernel
  qa_write(vmid, '/etc/systemd/network/10-kopsrox.network', f'''\
[Match]
Driver=virtio_net

[Link]
MTUBytes={network_mtu}

[Network]
Address={vmip(vmid)}/{network_mask}
Gateway={network_gw}
DNS={network_dns}
''')
  qa_exec(vmid, 'rm -f /etc/systemd/network/20-microvm-dhcp.network /etc/microvm-static-net')

  # fallback script + oneshot unit - networkd sometimes never claims the nic on microvm
  qa_exec(vmid, 'mkdir -p /root/scripts /etc/rancher/k3s /var/lib/rancher/k3s/server/manifests /etc/sudoers.d')
  qa_write(vmid, '/root/scripts/kopsrox-net.sh', f'''\
#!/bin/sh
for dev in /sys/class/net/*; do
  # skip virtual devices like lo/dummy0/sit0 - only real ( virtio ) nics have a device link
  [ -e $dev/device ] || continue
  dev=$(basename $dev)
  ip link set $dev mtu {network_mtu} up
  ip addr replace {vmip(vmid)}/{network_mask} dev $dev
  ip route replace default via {network_gw} dev $dev
done
''', '755')
  qa_write(vmid, '/etc/systemd/system/kopsrox-net.service', '''\
[Unit]
Description=kopsrox static network fallback
After=network.target

[Service]
Type=oneshot
ExecStart=/root/scripts/kopsrox-net.sh

[Install]
WantedBy=multi-user.target
''')
  qa_exec(vmid, 'systemctl enable kopsrox-net.service 2>/dev/null')

  # dns - no systemd-resolved in the image
  qa_write(vmid, '/etc/resolv.conf', f'nameserver {network_dns}\n')

  # hostname - must match vmnames for k3s node operations
  qa_write(vmid, '/etc/hostname', f'{vmname}\n')
  qa_exec(vmid, f'sed -i /{vmname}/d /etc/hosts; echo 127.0.1.1 {vmname} >> /etc/hosts')

  # fresh machine-id per clone - regenerated on boot
  qa_exec(vmid, 'rm -f /etc/machine-id /var/lib/dbus/machine-id; touch /etc/machine-id')

  # push k3s install scripts
  qa_write(vmid, '/root/scripts/k3s.sh', open('./lib/scripts/k3s.sh').read(), '755')
  qa_write(vmid, '/root/scripts/kopsrox.sh', kopsrox_sh(), '755')

  # server config and manifests - masters only
  if masterid <= vmid <= (masterid + 2):
    qa_write(vmid, '/etc/rancher/k3s/config.yaml', k3s_server_config())
    qa_write(vmid, f'/var/lib/rancher/k3s/server/manifests/kopsrox-{cluster_name}.yaml', kopsrox_manifest())

  # grow the root filesystem to the resized disk - partitionless ext4
  qa_exec(vmid, 'resize2fs /dev/vda 2>/dev/null')

  # create user
  qa_exec(vmid, f'useradd -m -s /bin/bash -G sudo {cloudinituser} 2>/dev/null; echo {cloudinituser}:{cloudinitpass} | chpasswd')
  qa_exec(vmid, f'mkdir -p /home/{cloudinituser}/.ssh')
  qa_write(vmid, f'/home/{cloudinituser}/.ssh/authorized_keys', f'{cloudinitsshkey}\n', '600')
  qa_exec(vmid, f'chown -R {cloudinituser}:{cloudinituser} /home/{cloudinituser}/.ssh')
  qa_write(vmid, f'/etc/sudoers.d/{cloudinituser}', f'{cloudinituser} ALL=(ALL) NOPASSWD:ALL\n', '440')

  # mark prepared and reboot into final state
  qa_exec(vmid, 'touch /etc/kopsrox-node-init-done')
  node_reboot_wait(vmid)

  # verify static ip applied
  ip_out = qa_exec(vmid, 'ip -4 addr show')
  if not re.search(vmip(vmid), ip_out):
    kmsg(kname, f'{vmname} static ip {vmip(vmid)} not configured', 'err')
    exit(0)

  # verify internet access
  internet_check(vmid)

  # install any extra packages
  if extra_packages:
    packages = extra_packages.replace(',', ' ')
    kmsg(kname, f'{vmname} installing {packages}')
    qa_exec(vmid, f'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq 2>/dev/null && apt-get install -y -qq {packages} 2>/dev/null')

# stop and destroy vm
def prox_destroy(vmid: int):

    kname = 'prox_destroy-vm'

    # get node and vmname
    vmname = vmnames[vmid]
    node = vms[vmid]

    # if destroying image
    if vmid == cluster_id:
      prox_task(prox.nodes(node).qemu(cluster_id).delete())
      return

    # power off and delete
    try:
      prox_task(prox.nodes(node).qemu(vmid).status.stop.post(),node)
      prox_task(prox.nodes(node).qemu(vmid).delete(),node)
      kmsg(kname, vmname)
    except Exception as e:
      kmsg(kname, f'unable to destroy {node}/{vmid}\n{e}', 'err')
      exit(0)

# clone
def clone(vmid):

  # check where this may called as a str
  vmid = int(vmid)

  # map network info
  ip = vmip(vmid) + '/' + network_mask

  # vm ram convert from G
  memory = vm_ram * 1024

  # hostname
  hostname = vmnames[vmid]

  # clone
  kmsg('proxmox_clone', f'building {hostname}')
  prox_task(prox.nodes(proxmox_node).qemu(cluster_id).clone.post(newid = vmid))

  # configure
  prox_task(prox.nodes(proxmox_node).qemu(vmid).config.post(
    name = hostname,
    onboot = 1,
    cores = vm_cpu,
    memory = memory,
    balloon = '0',
    net0 = (f'model=virtio,bridge={network_bridge}'),
    description = (f'{vmid}:{hostname}:{ip}') 
  ))

  # resize disk
  prox_task(prox.nodes(proxmox_node).qemu(vmid).resize.put(
    disk = 'scsi0',
    size = f'{vm_disk}G',
  ))

  # power on
  prox_task(prox.nodes(proxmox_node).qemu(vmid).status.start.post())

  # configure the node via the guest agent
  node_prepare(vmid)

# proxmox task blocker
def prox_task(task_id, node=proxmox_node):

  # define default status
  status = {"status": ""}

  # until task stopped
  try:
    while (status["status"] != "stopped"):
      status = prox.nodes(proxmox_node).tasks(task_id).status.get()
  except:
    kmsg('proxmox_task-status', f'unable to get task {task_id} node: {node}', 'err')
    exit(0)

  # if task not completed ok
  if not status["exitstatus"] == "OK":
    kmsg('proxmox_task-status', (f'task exited with non OK status ({status["exitstatus"]})\n' + task_log(task_id)), 'err')
    exit(0)

# returns the task log
def task_log(task_id, node=proxmox_node):

  # define empty log line
  logline = ''

  # for each value in list
  # assuming task_id is valid
  try:
    for log in prox.nodes(proxmox_node).tasks(task_id).log.get():

      # append log to logline
      logline += log['t'] + '\n'

    return(logline)
  except:
    kmsg('proxmox_task-log', f'failed to get log for task!', 'err')
    exit(0)

  # return string
  return(logline)

# internet checker
def internet_check(vmid):
  vmname = vmnames[vmid]
  internet_cmd = 'curl -s --retry 2 --retry-all-errors --connect-timeout 1 --max-time 2 www.google.com > /dev/null && echo ok || echo error'
  internet_check = qa_exec(vmid, internet_cmd)

  # if curl command fails
  if internet_check == 'error':
    kmsg('prox_netcheck', f'{vmname} internet access check failed', 'err')
    exit(0)
