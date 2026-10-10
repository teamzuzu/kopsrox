#!/usr/bin/env bash
# one time build of the kopsrox microvm kernel
# fetches pve-microvm's kernel builder + config, merges lib/scripts/kopsrox-kernel.config
# ( k3s needs vxlan/ipset/xt_* built in - the stock pve-microvm kernel lacks them )
# installs /usr/share/pve-microvm/vmlinuz-kopsrox + initrd-kopsrox
# requirements: build-essential flex bison libelf-dev bc libssl-dev wget cpio
set -e

UPSTREAM=https://raw.githubusercontent.com/rcarmo/pve-microvm/main
WORK=/tmp/kopsrox-kernel
INSTALL_DIR=/usr/share/pve-microvm

# repo root ( script lives in dev/ )
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# build-kernel.sh >= 0.3.27 sources ../tools/pve-microvm-env.sh ( which in turn
# sources its own lib/project-tmp.sh ), so the checkout layout has to be mirrored
rm -rf $WORK/kernel $WORK/tools
mkdir -p $WORK/kernel $WORK/tools/lib $WORK/pve-microvm
cd $WORK

# fetch upstream builder and BOTH config files it merges. the overlay is not
# optional here: build-kernel.sh merges it only 'if [ -f ]', so a missing file is
# silently skipped rather than an error, and the kernel quietly loses upstream's
# tun/netfilter/cgroup/landlock/erofs options. the installed package is not a
# substitute - it ships no pve-microvm-6.12.config, so that merge would silently
# skip too
# check-landlock.sh / check-erofs.sh are run against the merged .config and abort
# the build if upstream's options did not survive olddefconfig
for f in build-kernel.sh pve-microvm-6.12.config pve-microvm-overlay.config \
         check-landlock.sh check-erofs.sh; do
    curl -sfL $UPSTREAM/kernel/$f -o kernel/$f
done
curl -sfL $UPSTREAM/tools/pve-microvm-env.sh -o tools/pve-microvm-env.sh
curl -sfL $UPSTREAM/tools/lib/project-tmp.sh -o tools/lib/project-tmp.sh
chmod +x kernel/build-kernel.sh

# merge the kopsrox fragment on top of the upstream config. build-kernel.sh merges
# pve-microvm-6.12.config from its own dir first, then pve-microvm-overlay.config,
# and later values win - so append to the overlay ( the last one merged ) to keep
# kopsrox's options winning if upstream ever sets one of them differently
cat "$REPO/lib/scripts/kopsrox-kernel.config" >> kernel/pve-microvm-overlay.config

# pin the scratch root the env helper would otherwise resolve to on its own, and
# build into it - >= 0.3.27 rejects an --output that is not below its build dir
export PVE_MICROVM_TMP_ROOT=$WORK/pve-microvm
BUILD_OUT=$PVE_MICROVM_TMP_ROOT/build

# build - initrd lands next to the kernel as initrd-microvm
./kernel/build-kernel.sh --output $BUILD_OUT/vmlinuz-kopsrox

# install
sudo install -m 644 $BUILD_OUT/vmlinuz-kopsrox $INSTALL_DIR/vmlinuz-kopsrox
sudo install -m 644 $BUILD_OUT/initrd-microvm $INSTALL_DIR/initrd-kopsrox

# fingerprint of what this kernel was built from - kopsrox_config compares it to
# detect an upstream config change that leaves DEFAULT_VERSION alone. the byte
# stream must match KERNEL_BUILD_INPUTS there ( the fragment is already appended
# to the overlay by this point )
cat kernel/build-kernel.sh kernel/pve-microvm-6.12.config kernel/pve-microvm-overlay.config \
    | sha256sum | cut -c1-16 | sudo tee $INSTALL_DIR/vmlinuz-kopsrox.id > /dev/null

echo "installed $INSTALL_DIR/vmlinuz-kopsrox and $INSTALL_DIR/initrd-kopsrox"
