#!/usr/bin/env python3

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


# run a command in a vm via the guest agent. fatal=False returns '' instead of
# kaborting, for probes that poll while the agent may legitimately be down
def qa_exec(vmid: int = masterid, cmd: str = 'uptime', node: str = proxmox_node, timeout: int = 600, fatal: bool = True) -> str:

    kname = 'proxmox_qa-exec'

    def fail(msg: str) -> str:
        if fatal:
            kabort(kname, msg)
        return ''

    vmname = vmnames[vmid]

    # never show k3s tokens on screen
    safe_cmd = re.sub(r'K10\S+', '<token>', cmd)

    short_cmd = safe_cmd if len(safe_cmd) <= 60 else safe_cmd[:57] + '...'

    # synchronous, returns json. check=False - a non-zero qm exit is the agent
    # call failing, handled below; the in-guest code is inside the json
    exec_argv = ['qm', 'guest', 'exec', str(vmid), '--timeout', str(timeout), '--', 'bash', '-c', cmd]

    with kstep(kname, f'{vmname} {short_cmd}', quiet = True) as step:

        # one spawn on an already-up agent; only on failure ping and retry once
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

    if not result.get('exited'):
        return fail(f'timed out after {timeout}s on {vmname}: {safe_cmd}')

    if int(result.get('exitcode', 0)) == 127:
        return fail(f'exit code 127: {safe_cmd}')

    out = (result.get('out-data') or '').strip()
    err = (result.get('err-data') or '').strip()

    # stderr - report, but return stdout if there is any ( probes stay quiet )
    if err:
        if not fatal:
            return out
        kmsg('proxmox_qa-stderr', f'{safe_cmd}\n{err}', 'err')
        if out:
            return out
        exit(1)

    if out:
        return out
    return 'no output-' + cmd

def qa_write(vmid: int, remote_path: str, content: str, mode: str = '644') -> None:

    kname = 'proxmox_qa_write'

    # base64 over --pass-stdin ( 1 MiB cap ) so any bytes survive, mode included
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

def node_reboot_wait(vmid: int) -> None:

    kname = 'proxmox_reboot'
    vmname = vmnames[vmid]

    with kstep(kname, f'rebooting {vmname}'):

        # boot id, not agent-down polling - microvms reboot in about a second
        boot_id = qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id')

        qa_exec(vmid, 'systemd-run --on-active=1 systemctl reboot 2>/dev/null')

        # non fatal - the agent drops mid poll while the guest reboots
        for count in range(60):
            time.sleep(2)
            new_boot_id = qa_exec(vmid, 'cat /proc/sys/kernel/random/boot_id', fatal = False)
            if new_boot_id and new_boot_id != boot_id:
                break
        else:
            kabort(kname, f'{vmname} did not reboot')

# configure a freshly cloned microvm - works before networking is up
def node_prepare(vmid: int) -> None:

    kname = 'proxmox_prepare'
    vmname = vmnames[vmid]

    if qa_exec(vmid, 'test -f /etc/kopsrox-node-init-done && echo done || echo todo') == 'done':
        internet_check(vmid)
        return

    with kstep(kname, f'configuring {vmname}'):

        # one script in one qa_exec - each round trip costs a fixed ~0.5-1s. the
        # identity and network are applied LIVE: a reboot reaches the same state
        # but adds 8-35s of virtio-serial reconnect that can wedge the agent
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

        ip_out = qa_exec(vmid, 'ip -4 addr show')
        if not re.search(vmip(vmid), ip_out):
            kabort(kname, f'{vmname} static ip {vmip(vmid)} not configured')

        internet_check(vmid)

def prox_destroy(vmid: int) -> None:

    kname = 'proxmox_destroy'

    vmname = vmnames[vmid]

    if vmid == cluster_id:
        pve_run(['qm', 'destroy', str(cluster_id)], kname = kname)
        return

    # ignore a stop error for an already-stopped vm
    with kstep(kname, f'destroying {vmname}'):
        pve_run(['qm', 'stop', str(vmid)], check = False, kname = kname)
        pve_run(['qm', 'destroy', str(vmid)], kname = kname)

def clone(vmid: int) -> None:

    vmid = int(vmid)

    ip = vmip(vmid) + '/' + network_mask

    memory = vm_ram * 1024

    hostname = vmnames[vmid]

    with kstep('proxmox_clone', f'building {hostname}'):
        pve_run(['qm', 'clone', str(cluster_id), str(vmid)], kname = 'proxmox_clone')

        pve_run(['qm', 'set', str(vmid),
                 '--name', hostname,
                 '--onboot', '1',
                 '--cores', str(vm_cpu),
                 '--memory', str(memory),
                 '--balloon', '0',
                 '--net0', f'model=virtio,bridge={network_bridge}',
                 '--description', f'{vmid}:{hostname}:{ip}'], kname = 'proxmox_clone')

        pve_run(['qm', 'resize', str(vmid), 'scsi0', f'{vm_disk}G'], kname = 'proxmox_clone')

        pve_run(['qm', 'start', str(vmid)], kname = 'proxmox_clone')

    node_prepare(vmid)

    kplan_tick()

def internet_check(vmid: int) -> None:
    vmname = vmnames[vmid]
    internet_cmd = 'curl -s --retry 2 --retry-all-errors --connect-timeout 1 --max-time 2 www.google.com > /dev/null && echo ok || echo error'

    # a fresh node's route/dns can take a moment, so a single miss is not failure
    for attempt in range(5):
        if qa_exec(vmid, internet_cmd) == 'ok':
            return
        time.sleep(1)

    kabort('proxmox_netcheck', f'{vmname} internet access check failed')
