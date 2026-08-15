#!/usr/bin/env python3

# kopsrox
import re
import time

from kopsrox_config import (
    cluster_id,
    masterid,
    network_bridge,
    network_dns,
    network_gw,
    network_mask,
    network_mtu,
    prox,
    proxmox_node,
    vm_cpu,
    vm_disk,
    vm_ram,
    vmip,
    vmnames,
    vms,
)
from kopsrox_kmsg import kabort, kmsg, kplan_tick, kstep


# run a exec via qemu-agent
# fatal = False turns every failure into an empty return instead of kabort -
# for probes that poll while the agent may legitimately be down ( eg mid reboot )
def qa_exec(vmid: int = masterid, cmd: str = 'uptime', node: str = proxmox_node, timeout: int = 600, fatal: bool = True) -> str:

    # define kname
    kname = 'proxmox_qa-exec'

    # abort or return empty depending on fatal
    def fail(msg: str) -> str:
        if fatal:
            kabort(kname, msg)
        return ''

    # get vmname and the node the vm actually runs on
    vmname = vmnames[vmid]
    node = vms.get(vmid, node)

    # display copy of the command - never show k3s tokens on screen
    safe_cmd = re.sub(r'K10\S+', '<token>', cmd)

    # short command for the live line
    short_cmd = safe_cmd if len(safe_cmd) <= 60 else safe_cmd[:57] + '...'

    with kstep(kname, f'{vmname} waiting for agent', quiet = True) as step:

        # wait for the agent - can be slow on first boot
        for _ in range(120):
            try:
                prox.nodes(node).qemu(vmid).agent.ping.post()
                break
            except Exception:
                time.sleep(1)
        else:
            return fail(f'agent not responding on {vmname} [{node}] cmd: {safe_cmd}')

        # agent is up - show the command while it runs
        step.msg = f'{vmname} {short_cmd}'

        # send command
        try:
            exec_ret = prox.nodes(node).qemu(vmid).agent.exec.post(command = "bash -c '" + cmd + "'")
        except Exception as e:
            return fail(f'problem running cmd: {safe_cmd}\n{e}')

        # poll until the command exits
        pid = exec_ret['pid']
        waited = float(0)
        while True:
            try:
                pid_check = prox.nodes(node).qemu(vmid).agent('exec-status').get(pid = pid)
            except Exception as e:
                return fail(f'problem with pid: {pid} {safe_cmd}\n{e}')
            if pid_check['exited'] == 1:
                break
            time.sleep(0.5)
            waited += 0.5
            if waited >= timeout:
                return fail(f'timed out after {timeout}s on {vmname}: {safe_cmd}')

    # check for exitcode 127
    if int(pid_check['exitcode']) == 127:
        return fail(f'exit code 127: {pid} {safe_cmd}')

    out = (pid_check.get('out-data') or '').strip()
    err = (pid_check.get('err-data') or '').strip()

    # stderr - report and return stdout if there is any ( probes stay quiet )
    if err:
        if not fatal:
            return out
        kmsg('proxmox_qa-stderr', f'{safe_cmd}\n{err}', 'err')
        if out:
            return out
        exit(1)

    # this is where data gets returned for an OK command
    if out:
        return out
    return 'no output-' + cmd

# write a file into a vm via the guest agent
def qa_write(vmid: int, remote_path: str, content: str, mode: str = '644') -> None:

    # define kname
    kname = 'proxmox_qa_write'

    # get node for vm - fallback to configured node for new clones
    try:
        node = vms[vmid]
    except Exception:
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

    except Exception:
        kabort(kname, f'unable to write {remote_path} to {vmnames[vmid]}')

    # set permissions
    qa_exec(vmid, f'chmod {mode} {remote_path}')

# reboot a node via the agent and wait for it to return
def node_reboot_wait(vmid: int) -> None:

    # define kname
    kname = 'proxmox_reboot'
    vmname = vmnames[vmid]

    with kstep(kname, f'rebooting {vmname}'):

        # note the current boot id - microvms reboot in about a second so watching
        # for the agent to go down is a race we can lose
        boot_id = qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id')

        # transient timer so the exec returns before the agent goes away
        qa_exec(vmid, 'systemd-run --on-active=1 systemctl reboot 2>/dev/null')

        # wait for a new boot id - non fatal probe as the agent drops mid poll
        # while the guest reboots ( a fatal qa_exec would abort the whole run )
        for count in range(60):
            time.sleep(2)
            new_boot_id = qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id', fatal = False)
            if new_boot_id and new_boot_id != boot_id:
                break
        else:
            kabort(kname, f'{vmname} did not reboot')

# configure a newly cloned microvm via the guest agent
# the agent runs over virtio-serial so this works before networking is up
def node_prepare(vmid: int) -> None:

    # define kname
    kname = 'proxmox_prepare'
    vmname = vmnames[vmid]

    # skip already prepared nodes
    if qa_exec(vmid, 'test -f /etc/kopsrox-node-init-done && echo done || echo todo') == 'done':
        internet_check(vmid)
        return

    with kstep(kname, f'configuring {vmname}'):

        # every step below is bundled into one script and run via a single
        # qa_exec call - each guest-agent round trip has a fixed ~0.5-1s protocol
        # cost regardless of what it does, and this used to be ~15 separate calls
        # ( qa_write() alone is a write + a chmod ). identity ( machine-id,
        # hostname ) and the static network are applied LIVE at the end of the
        # script - no reboot: the agent is on virtio-serial so it survives the
        # ip swap, and a reboot only added 8-35s of variable virtio-serial
        # reconnect latency ( sometimes wedging the agent ) for no change in the
        # resulting state ( verified: a later reboot converges to the same thing )
        prepare_sh = f'''\
#!/bin/sh
# neutralise pve-microvm first boot services
# microvm-setup waits for network then installs cloud-init/docker
# microvm-static-net regenerates network config every boot
systemctl disable --now microvm-setup.service 2>/dev/null
touch /etc/microvm-setup-done
systemctl disable microvm-static-net.service 2>/dev/null

# static network via systemd-networkd
# match on driver - Type=ether also matches dummy0/sit0 from the kopsrox kernel
cat > /etc/systemd/network/10-kopsrox.network <<'KOPSROX_EOF'
[Match]
Driver=virtio_net

[Link]
MTUBytes={network_mtu}

[Network]
Address={vmip(vmid)}/{network_mask}
Gateway={network_gw}
DNS={network_dns}
KOPSROX_EOF
chmod 644 /etc/systemd/network/10-kopsrox.network
rm -f /etc/systemd/network/20-microvm-dhcp.network /etc/microvm-static-net

# fallback script + oneshot unit - networkd sometimes never claims the nic on microvm
mkdir -p /root/scripts
cat > /root/scripts/kopsrox-net.sh <<'KOPSROX_EOF'
#!/bin/sh
for dev in /sys/class/net/*; do
  # skip virtual devices like lo/dummy0/sit0 - only real ( virtio ) nics have a device link
  [ -e $dev/device ] || continue
  dev=$(basename $dev)
  ip link set $dev mtu {network_mtu} up
  ip addr replace {vmip(vmid)}/{network_mask} dev $dev
  ip route replace default via {network_gw} dev $dev
done
KOPSROX_EOF
chmod 755 /root/scripts/kopsrox-net.sh
cat > /etc/systemd/system/kopsrox-net.service <<'KOPSROX_EOF'
[Unit]
Description=kopsrox static network fallback
After=network.target

[Service]
Type=oneshot
ExecStart=/root/scripts/kopsrox-net.sh

[Install]
WantedBy=multi-user.target
KOPSROX_EOF
chmod 644 /etc/systemd/system/kopsrox-net.service
systemctl enable kopsrox-net.service 2>/dev/null

# hostname - must match vmnames for k3s node operations
# set live too ( hostnamectl ) so the running system picks it up without a reboot
echo '{vmname}' > /etc/hostname
chmod 644 /etc/hostname
sed -i /{vmname}/d /etc/hosts
echo 127.0.1.1 {vmname} >> /etc/hosts
hostnamectl set-hostname '{vmname}' 2>/dev/null

# fresh, unique machine-id per clone - committed now ( systemd-machine-id-setup )
# rather than blanked for boot-time regen, since nothing reboots this node
rm -f /etc/machine-id /var/lib/dbus/machine-id
systemd-machine-id-setup
ln -sf /etc/machine-id /var/lib/dbus/machine-id

# grow the root filesystem to the resized disk - partitionless ext4
resize2fs /dev/vda 2>/dev/null

# apply the static network live - reconfigure networkd, then the iproute2
# fallback ( idempotent ip replace ) guarantees the address/route are up
# synchronously so the verification below passes without waiting on networkd
systemctl enable --now systemd-networkd 2>/dev/null
networkctl reload 2>/dev/null
networkctl reconfigure eth0 2>/dev/null
/root/scripts/kopsrox-net.sh 2>/dev/null

# mark prepared - the node is now in its final state, no reboot needed
touch /etc/kopsrox-node-init-done
exit 0
'''
        qa_write(vmid, '/root/kopsrox-prepare.sh', prepare_sh, '755')
        qa_exec(vmid, '/root/kopsrox-prepare.sh > /kopsrox-prepare.log 2>&1; rm -f /root/kopsrox-prepare.sh')

        # verify static ip applied
        ip_out = qa_exec(vmid, 'ip -4 addr show')
        if not re.search(vmip(vmid), ip_out):
            kabort(kname, f'{vmname} static ip {vmip(vmid)} not configured')

        # verify internet access
        internet_check(vmid)

# stop and destroy vm
def prox_destroy(vmid: int) -> None:

    kname = 'proxmox_destroy'

    # get node and vmname
    vmname = vmnames[vmid]
    node = vms[vmid]

    # if destroying image
    if vmid == cluster_id:
        prox_task(prox.nodes(node).qemu(cluster_id).delete(), node)
        return

    # power off and delete
    with kstep(kname, f'destroying {vmname}'):
        try:
            prox_task(prox.nodes(node).qemu(vmid).status.stop.post(), node)
            prox_task(prox.nodes(node).qemu(vmid).delete(), node)
        except Exception as e:
            kabort(kname, f'unable to destroy {node}/{vmid}\n{e}')

# clone
def clone(vmid: int) -> None:

    # check where this may called as a str
    vmid = int(vmid)

    # map network info
    ip = vmip(vmid) + '/' + network_mask

    # vm ram convert from G
    memory = vm_ram * 1024

    # hostname
    hostname = vmnames[vmid]

    # clone
    with kstep('proxmox_clone', f'building {hostname}'):
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

    # one plan unit - clone + prepare
    kplan_tick()

# proxmox task blocker
def prox_task(task_id: str, node: str = proxmox_node, timeout: int = 600) -> None:

    # define kname
    kname = 'proxmox_task'

    # task type out of the upid for the live line
    try:
        task_type = task_id.split(':')[5]
    except Exception:
        task_type = str(task_id)

    # poll until task stopped
    with kstep(kname, f'{task_type} on {node}', quiet = True):
        waited = float(0)
        while True:
            try:
                status = prox.nodes(node).tasks(task_id).status.get()
            except Exception as e:
                kabort(kname, f'unable to get task {task_id} node: {node}\n{e}')
            if status['status'] == 'stopped':
                break
            time.sleep(0.5)
            waited += 0.5
            if waited >= timeout:
                kabort(kname, f'{task_type} timed out after {timeout}s on {node}')

    # if task not completed ok
    if not status['exitstatus'] == 'OK':
        kabort(kname, (f'task exited with non OK status ({status["exitstatus"]})\n' + task_log(task_id, node)))

# returns the task log
def task_log(task_id: str, node: str = proxmox_node) -> str:

    # define empty log line
    logline = ''

    # append each log line - a log fetch failure must not mask the task error
    try:
        for log in prox.nodes(node).tasks(task_id).log.get():
            logline += log['t'] + '\n'
    except Exception:
        kmsg('proxmox_task-log', f'failed to get log for task {task_id}', 'sys')

    # return string
    return logline

# internet checker
def internet_check(vmid: int) -> None:
    vmname = vmnames[vmid]
    internet_cmd = 'curl -s --retry 2 --retry-all-errors --connect-timeout 1 --max-time 2 www.google.com > /dev/null && echo ok || echo error'

    # retry up to 5 times - a freshly configured node's route/dns can take a
    # moment to settle, so a single miss is not yet a failure
    for attempt in range(5):
        if qa_exec(vmid, internet_cmd) == 'ok':
            return
        time.sleep(1)

    kabort('proxmox_netcheck', f'{vmname} internet access check failed')
