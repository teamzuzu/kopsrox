#!/usr/bin/env python3

# kopsrox
import base64
import json
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
    proxmox_node,
    pve_run,
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

    # get vmname ( node is always the local one - kept in the signature for callers )
    vmname = vmnames[vmid]

    # display copy of the command - never show k3s tokens on screen
    safe_cmd = re.sub(r'K10\S+', '<token>', cmd)

    # short command for the live line
    short_cmd = safe_cmd if len(safe_cmd) <= 60 else safe_cmd[:57] + '...'

    # qm guest exec is synchronous ( waits up to --timeout ) and returns the guest
    # result as json - out-data / err-data / exitcode / exited. argv list so cmd
    # ( heredocs, quotes, newlines ) needs no escaping. check=False: a non-zero qm
    # exit is the agent call itself failing ( eg agent not up yet ), handled below;
    # the in-guest exit code is inside the json
    exec_argv = ['qm', 'guest', 'exec', str(vmid), '--timeout', str(timeout), '--', 'bash', '-c', cmd]

    with kstep(kname, f'{vmname} {short_cmd}', quiet = True) as step:

        # try the exec straight away - on an already-up agent ( the common case )
        # this is a single qm spawn, no separate ping first. only if it fails do
        # we assume the agent is not up yet ( eg first boot after a clone ), wait
        # for qm agent ping, then retry once
        cp = pve_run(exec_argv, check = False, kname = kname)
        if cp.returncode != 0:
            step.msg = f'{vmname} waiting for agent'
            for _ in range(120):
                if pve_run(['qm', 'agent', str(vmid), 'ping'], check = False, kname = kname).returncode == 0:
                    break
                time.sleep(1)
            else:
                return fail(f'agent not responding on {vmname} cmd: {safe_cmd}')
            step.msg = f'{vmname} {short_cmd}'
            cp = pve_run(exec_argv, check = False, kname = kname)
            if cp.returncode != 0:
                return fail(f'agent exec failed on {vmname}: {safe_cmd}\n{cp.stderr.strip()}')

        try:
            result = json.loads(cp.stdout)
        except Exception:
            return fail(f'could not parse agent result on {vmname}: {safe_cmd}\n{cp.stdout}')

    # command still running when --timeout hit ( qm returns the pid, not exited )
    if not result.get('exited'):
        return fail(f'timed out after {timeout}s on {vmname}: {safe_cmd}')

    # check for exitcode 127
    if int(result.get('exitcode', 0)) == 127:
        return fail(f'exit code 127: {safe_cmd}')

    out = (result.get('out-data') or '').strip()
    err = (result.get('err-data') or '').strip()

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

    # base64 the content and feed it to the guest agent over stdin ( --pass-stdin,
    # max 1 MiB ) into base64 -d - handles arbitrary bytes/quotes/newlines with no
    # escaping and sets the mode in the same call ( one round trip; was a write +
    # a chmod ). all kopsrox artifacts are well under 1 MiB so no chunking needed
    b64 = base64.b64encode(content.encode()).decode()
    if len(b64) > 1024 * 1024:
        kabort(kname, f'{remote_path} too large for a single agent write ({len(b64)} b64 bytes > 1 MiB)')

    cp = pve_run(['qm', 'guest', 'exec', str(vmid), '--pass-stdin', '1', '--timeout', '60',
                  '--', 'bash', '-c', f'base64 -d > {remote_path} && chmod {mode} {remote_path}'],
                 input = b64, check = False, kname = kname)

    ok = False
    if cp.returncode == 0:
        try:
            ok = int(json.loads(cp.stdout).get('exitcode', 1)) == 0
        except Exception:
            ok = False
    if not ok:
        kabort(kname, f'unable to write {remote_path} to {vmnames[vmid]}')

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

# journald already created /var/log/journal/<old-id>/ under the pre-regen
# machine-id ( persistent since /var/log/journal exists ); journalctl only
# reads /var/log/journal/<current-id>/, so it reports "No journal files were
# found" until this is reset. the pre-clone journal is just image-build noise,
# so purge it and let journald recreate the dir under the new machine-id
rm -rf /var/log/journal/*
systemctl restart systemd-journald

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

    # get vmname
    vmname = vmnames[vmid]

    # if destroying image ( never running )
    if vmid == cluster_id:
        pve_run(['qm', 'destroy', str(cluster_id)], kname = kname)
        return

    # power off ( qm is synchronous; ignore a stop error for an already-stopped
    # vm ) then delete
    with kstep(kname, f'destroying {vmname}'):
        pve_run(['qm', 'stop', str(vmid)], check = False, kname = kname)
        pve_run(['qm', 'destroy', str(vmid)], kname = kname)

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

    # clone ( qm is synchronous - each call returns when the task is done )
    with kstep('proxmox_clone', f'building {hostname}'):
        pve_run(['qm', 'clone', str(cluster_id), str(vmid)], kname = 'proxmox_clone')

        # configure
        pve_run(['qm', 'set', str(vmid),
                 '--name', hostname,
                 '--onboot', '1',
                 '--cores', str(vm_cpu),
                 '--memory', str(memory),
                 '--balloon', '0',
                 '--net0', f'model=virtio,bridge={network_bridge}',
                 '--description', f'{vmid}:{hostname}:{ip}'], kname = 'proxmox_clone')

        # resize disk ( absolute size )
        pve_run(['qm', 'resize', str(vmid), 'scsi0', f'{vm_disk}G'], kname = 'proxmox_clone')

        # power on
        pve_run(['qm', 'start', str(vmid)], kname = 'proxmox_clone')

    # configure the node via the guest agent
    node_prepare(vmid)

    # one plan unit - clone + prepare
    kplan_tick()

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
