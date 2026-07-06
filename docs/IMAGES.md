# images

kopsrox nodes are microvms built from OCI images via pve-microvm-template

set `oci_image` in kopsrox.ini - the default is `ubuntu:24.04`

other apt based images ( eg `debian:trixie-slim` ) should also work but are untested with kopsrox

nodes boot the kopsrox kernel ( `/usr/share/pve-microvm/vmlinuz-kopsrox` ) built by `dev/build-kopsrox-kernel.sh` - the image only provides the userland
