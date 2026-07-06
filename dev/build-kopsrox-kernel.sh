#!/usr/bin/env bash
# one time build of the kopsrox microvm kernel
# fetches pve-microvm's kernel builder + config, merges lib/scripts/kopsrox-kernel.config
# ( k3s needs vxlan/ipset/xt_* built in - the stock pve-microvm kernel lacks them )
# installs /usr/share/pve-microvm/vmlinuz-kopsrox + initrd-kopsrox
# requirements: build-essential flex bison libelf-dev bc libssl-dev wget cpio
set -e

UPSTREAM=https://raw.githubusercontent.com/rcarmo/pve-microvm/main/kernel
WORK=/tmp/kopsrox-kernel
INSTALL_DIR=/usr/share/pve-microvm

# repo root ( script lives in dev/ )
REPO="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p $WORK
cd $WORK

# fetch upstream builder and base config
curl -sfLO $UPSTREAM/build-kernel.sh
curl -sfLO $UPSTREAM/pve-microvm-6.12.config
chmod +x build-kernel.sh

# merge kopsrox fragment on top of the upstream config
# build-kernel.sh merges pve-microvm-6.12.config from its own dir - later values win
cat "$REPO/lib/scripts/kopsrox-kernel.config" >> pve-microvm-6.12.config

# build - initrd lands next to the kernel as initrd-microvm
./build-kernel.sh --output $WORK/vmlinuz-kopsrox

# install
sudo install -m 644 $WORK/vmlinuz-kopsrox $INSTALL_DIR/vmlinuz-kopsrox
sudo install -m 644 $WORK/initrd-microvm $INSTALL_DIR/initrd-kopsrox

echo "installed $INSTALL_DIR/vmlinuz-kopsrox and $INSTALL_DIR/initrd-kopsrox"
