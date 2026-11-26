---
name: update-kopsrox-microvm
description: Update kopsrox compatibility and adopt useful pve-microvm features when rcarmo/pve-microvm publishes a release. Use for release-driven updates, compatibility checks, and deciding whether a new upstream capability belongs in kopsrox.
---

# Update kopsrox for a pve-microvm release

Work from the kopsrox checkout. Treat its Proxmox configuration as live. Use temporary directories and extracted package data for routine validation. Installing the package, running its maintainer scripts, replacing host kernels, and running lifecycle tests against real VMs require authorization for the target environment.

## Establish the release delta

- Read the [upstream releases](https://github.com/rcarmo/pve-microvm/releases). Use the requested tag or the latest published stable release. Find the previous kopsrox compatibility claim in README.md and confirm its basis in git history. Keep the minimum supported version, last reviewed release, and installed Debian package version distinct.
- Compare the previous reviewed tag with the target, covering intervening releases. Inspect the tagged source, release notes, and published assets, including packaging and Proxmox version requirements. Match the source to the actual installed paths and behavior by extracting the published Debian package with `dpkg-deb -x` and its control files with `dpkg-deb -e` when available. Verify published checksums when provided.
- If the target is already reviewed, check for unresolved compatibility or adoption work before deciding whether changes are needed. Record any missing release evidence.

## Follow the affected contracts

- **Template creation.** In `lib/verb_image.py`, exercise `patch_microvm_template()` against the shipped `pve-microvm-template`. Check every replacement and its resulting behavior. Review template CLI flags, image refresh and conversion ordering, rootfs packages and DNS, console units, and storage defaults. Preserve the k3s provisioning changes when switching to any new upstream hook.
- **Guest boot and node lifecycle.** Review `lib/kopsrox_proxmox.py`, `lib/verb_node.py`, and affected cluster paths for changes in clone, resize, boot arguments, guest agent, first-boot units, NIC naming, and network rules. Check custom kernel and initrd paths and existing templates and guests. Account for host-side features that already benefit existing kopsrox commands through Proxmox.
- **Kernel builder and drift.** Review `dev/build-kopsrox-kernel.sh`, `lib/scripts/kopsrox-kernel.config`, and `lib/kopsrox_config.py`. Trace builder dependencies, output paths, boot and security defaults, and k3s features required as built-ins. Keep the fingerprint inputs and ordering identical between the builder and drift check, including the local fragment. Use tagged inputs to establish release compatibility and account for the existing runtime use of upstream main. Keep release and kernel network checks scoped to `config check`.
- **Version and configuration.** Check the image creation gate, package-version parsing, schema defaults, and upgrade behavior. Verify existing compatibility claims against the supported package variants before changing the minimum. Prefer a proven compatible path or capability detection when one is practical. Document any necessary minimum-version change and migration.

## Implement and verify

- Implement the required compatibility changes and adopt upstream capabilities that improve an existing kopsrox workflow or fit its documented scope. For each relevant capability, determine whether it needs a code change, works through the existing path, or needs prerequisites or validation before adoption. Check guest-side effects independently of host-side behavior. For example, a live block resize and guest filesystem growth are separate operations.
- Run the existing offline tests in `dev/test_*.py` from the repository root and syntax-check changed Python and shell files. For a changed template contract, test the normal patch function with temporary packaged fixtures and syntax-check the patched shell output. Test the affected older supported path as well as the target. Use fixtures or mocks for changed Proxmox arguments or package behavior. Exercise an isolated kernel build or disposable Proxmox lifecycle for changed boot, network, storage, or guest-agent behavior when the environment and authorization permit it.
- Update README.md with the upstream release link, supported versions, and any user-facing feature or upgrade steps warranted by the evidence. Identify when a package update, kernel rebuild, template rebuild, or guest restart is needed. Describe package inspection, fixture testing, and live verification accurately.
- Review the final diff. Report the tags compared, implemented or inherited features, deferred items with their specific reasons, and validation performed. State any remaining live validation with the affected operation. Follow the caller's instructions for committing, publishing, or deployment.
