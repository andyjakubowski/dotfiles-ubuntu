# Disposable GPU VM: setup and daily use

This guide is the operating procedure; [the architecture guide](gpu-vm-explained.md)
explains the mechanisms. The implementation is prepared and tested offline, but
has not yet been installed or verified with a running GPU guest on ai-box.

## What dotfiles does

`DOTFILES_CODEX_VM=1 ./setup_codex_vm.sh` prepares the **host infrastructure**.
It does not create a VM, detach a live GPU, change firmware settings or reboot.
Ordinary `./install` runs skip this optional setup; setting the same opt-in on
`./install` includes it in the full dotfiles run.

The setup installs Ubuntu QEMU/libvirt, OVMF firmware, cloud-image tools,
libguestfs tools and their dependencies. It installs a root-owned `codex-vm`
command, saves configuration under `/etc/codex-vm`, and enables libvirt plus a
firewall service. It creates a private NAT network/bridge (`virbr-codex`,
`192.168.250.0/24`) with DHCP/DNS; libvirt manages forwarding and NAT. Existing
physical NICs and NetworkManager profiles are unchanged. Package installation
may also activate libvirt's default network.

The dedicated firewall table is replaced atomically; unrelated tables are not
flushed. New guest connections to host services are blocked except bridge
DNS/DHCP. Guest forwarding to private/link-local/CGNAT/reserved IPv4 destinations
and all guest IPv6 forwarding is blocked. Public internet destinations remain
allowed. Guest bridge ports are isolated from each other. Other host firewall
rules can further restrict traffic. Keep these services enabled; a host-side
firewall flush can remove the boundary and needs reconciliation.

No host user gets broad libvirt privileges, and no host sudo policy is changed.
Management uses explicit host sudo. No physical SSD is partitioned or formatted.
Guest disks are files on the **system SSD**, not the data SSD. All of the mounted
`/mnt/data` is intentionally readable in the guest through a **host-enforced
read-only QEMU 9p export**, not writable host disk passthrough. This also exposes
any non-model data there; only share files the guest may read. Ubuntu 24.04's
libvirt lacks native read-only virtiofs support, so 9p is the supported alternative.
It can be slower than virtiofs for model loading; GPU computation is separate.
Model files mapped by running workloads should not be modified on the host.

Defaults in `provision/codex-vm/config.sh`: 8 vCPUs, 16384 MiB RAM, 100 GiB guest
disk, the only detected NVIDIA GPU, `nvidia-driver-575-open` in the guest (Ubuntu
may transition this package to a newer supported branch). The public keys already
in `~/.ssh/authorized_keys` authorize the guest. Private keys are never copied.
Override with `CODEX_VM_CPUS`, `CODEX_VM_MEMORY_MIB`, `CODEX_VM_DISK_GIB`,
`CODEX_VM_GPU_BDF`, `CODEX_VM_NVIDIA_PACKAGE`, or `CODEX_VM_SSH_PUBLIC_KEY`.
The latter must name a file of plain public keys, not keys with authorization options.
Defaults and recipes live in Git; per-machine public keys are read at setup time.

## 1. Prepare the host and reserve the GPU

On ai-box, from the repository:

```sh
DOTFILES_CODEX_VM=1 ./setup_codex_vm.sh
sudo codex-vm inspect
```

The current machine has a 5090 at `0000:01:00.0`, its audio function at
`0000:01:00.1`, and no unrelated devices in their IOMMU group. AMD integrated
graphics is available. **The 5090 is currently the firmware's primary boot GPU**
and hosts GNOME/Xorg, even though you normally access the machine headlessly.

Set **integrated graphics as the primary display in UEFI**, then reboot. Exact
firmware labels vary by motherboard. Move any host monitor cable to the
motherboard output if needed. Inspect again; `boot_vga` for the 5090 must be `0`.
CPU virtualization and IOMMU already work on the inspected machine.

Then:

```sh
sudo codex-vm prepare-gpu --acknowledge-display-loss
```

This checks device isolation and unique PCI IDs, writes only
`/etc/modprobe.d/codex-vm-vfio.conf` and
`/etc/initramfs-tools/hooks/codex-vm-vfio`, and rebuilds installed initramfs images.
These arrange early binding of the GPU/audio to `vfio-pci`. It does not unbind
running drivers or change GRUB. If firmware still selects the 5090 for boot, it
refuses rather than disabling host framebuffers globally.

Reboot manually, then run `sudo codex-vm inspect`: both functions must show
`driver=vfio-pci`. The GPU stays reserved for guests even when none is running;
host NVIDIA workloads, including host Ollama GPU inference, cannot use it.

## 2. Create and verify a clean guest

```sh
sudo codex-vm create builder
sudo codex-vm list
```

Creation downloads Canonical's Ubuntu 24.04 cloud image and verifies its SHA256
against Canonical's HTTPS checksum listing. It creates an independent guest disk,
a seed ISO and a persistent libvirt definition, reserves an unused IP/MAC, attaches
both GPU functions plus the read-only share, and boots the guest. Each guest gets
fresh cloud-init metadata and network configuration. OVMF boots with Secure Boot
disabled so the guest's NVIDIA module does not require interactive key enrollment;
a 64 GiB PCI MMIO window accommodates the 5090's large BAR. Guests do not autostart.

The command prints the guest IP. Add an alias on your **MBP**, using that IP and
the private key corresponding to the public key already accepted by ai-box:

```sshconfig
Host codex-builder
    HostName 192.168.250.2
    User codex
    ProxyJump ai-box
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ForwardAgent no
```

Your existing `ai-box` alias still reaches host `10.77.77.2`. No agent or private
key is forwarded. The host SSH service must allow TCP forwarding to the guest.
The example IP assumes the first available slot; always use the printed address.

```sh
ssh codex-builder 'cloud-init status --wait'
ssh codex-builder 'test -f /var/lib/codex-vm-provisioned && ~/.local/bin/codex --version'
ssh codex-builder 'sudo reboot'
```

The guest installs a kernel/headers, open NVIDIA driver, Git, Python and build
tools, plus Codex. Reboot it before testing the GPU, because the installed kernel
may be newer than the kernel used during first boot. Then check:

```sh
ssh codex-builder 'nvidia-smi; findmnt /mnt/data'
ssh codex-builder 'curl --fail --max-time 15 https://example.com >/dev/null'
sudo virsh --connect qemu:///system dumpxml builder
sudo nft list table inet codex_vm
```

Run SSH commands on the MBP and the sudo commands on ai-box. Require successful
`nvidia-smi` showing the 5090, a 9p `ro` mount, and successful public HTTPS.
Inspect XML for exactly the GPU functions and read-only `/mnt/data` filesystem,
with no host block disks or guest management/SSH-agent channels. As guest root,
verify that remounting the share writable is rejected or that the backend still
rejects a write, then restore the `ro` guest mount. Avoid overwriting real files.
Test bounded connections to an actual listening host/LAN service: they should
time out and increase the corresponding firewall drop counter. Check physical
host IPs and Tailscale too; repeat boundary checks after a host reboot.

Direct GPU assignment targets near-native performance, not guaranteed zero
penalty. Benchmark your actual workload against a recorded host baseline;
also test guest shutdown/start to verify GPU reset/reassignment. These hardware
and end-to-end checks remain outstanding until provisioning is run.

For failures, inspect guest `/var/log/cloud-init-output.log`; use
`sudo virsh --connect qemu:///system console builder` for boot messages (Ctrl+]
exits; password login is disabled). Failed creation retains partial state/disks
for inspection and never silently deletes or adopts them.

## 3. Save a reusable base, then work in disposable clones

Do this while `builder` is a clean provisioned guest, **before personal login,
projects or secrets are added**. A template is not a general secrets scrubber.

```sh
sudo codex-vm stop builder
sudo codex-vm list
# Once builder is shut off:
sudo codex-vm template builder ubuntu-gpu
sudo codex-vm create work --base ubuntu-gpu
```

`template` copies the stopped disk to an independent base, clears cloud-init
instance state, machine ID, SSH host/user keys, Codex auth/config and common logs.
It never modifies the original guest disk or overwrites an existing base. The
base is read-only to QEMU; clones store changes in their own QCOW2 overlay.
Retain the base while clones reference it. There is no automatic base deletion.

Use the printed IP for an MBP `codex-work` SSH alias. First-boot cloud-init supplies
fresh identity, client public keys and guest-only full-access Codex settings;
installed tools/drivers are reused. Connect the desktop app to that SSH alias,
select `/home/codex/workspace`, and authenticate Codex inside the guest. The
workstation's `.codex/config.toml` allows unrestricted commands **in the guest**.
The host's Codex config is never changed. Guest credentials are readable by guest
code, so grant only the account/service access you want this environment to have.

| Action on ai-box | Effect |
| --- | --- |
| `sudo codex-vm list` | Show domains, guest IPs and whether GPU assignment is configured |
| `sudo codex-vm stop work` | Request graceful shutdown; wait for shut-off state |
| `sudo codex-vm start work` | Resume the same disk; check mount, boundary and exclusive GPU ownership |
| `sudo codex-vm discard work --yes` | Delete only that stopped managed guest's definition, NVRAM, seed, disk and DHCP reservation |
| `sudo codex-vm create work --base ubuntu-gpu` | Create a fresh disposable instance; inspect its new address |

Export results with Git or client-side `scp` before discard. New downloads live
on the disposable disk; the original `/mnt/data` survives. A replacement can reuse
an old IP and has a new SSH host key: verify that you recreated it before updating
the MBP's known-host entry. Only one running guest can own the 5090. Additional
CPU-only builders are supported with `create NAME --cpu-only`.

## Reruns, storage and recovery

Host reruns reconcile packages/configuration/services without recreating guests.
Defaults/public-key changes apply to **new** guests; existing guests keep their
saved configuration. Host GPU hardware changes require explicit review rather
than updating old driver bindings blindly. Do not run host setup concurrently
with lifecycle operations; both use the same lock.

- `/etc/codex-vm/`: host configuration and dedicated firewall rules.
- `/usr/local/lib/codex-vm/`, `/usr/local/sbin/codex-vm`: root-owned helper code.
- `/var/lib/codex-vm/instances/`: public seed inputs, machine records and definitions.
- `/var/lib/libvirt/images/codex-vm/`: writable instance disks and immutable bases.
- `/var/cache/codex-vm/`: checksum-addressed downloaded images.

All are outside Git. Images are sparse but can grow to configured capacity; the
helper checks headroom and refuses overwrites. APT/image/installer releases can
change over time, so the recipe is repeatable, not a bit-identical build.

To return the GPU to host use, stop assigned-device guests, run
`sudo codex-vm unprepare-gpu --acknowledge-display-loss`, then reboot. This removes
only this setup's VFIO reservation and rebuilds initramfs; it does not change
UEFI primary display settings. Keep SSH access and use the AMD output for local
recovery. Nothing automatically reboots, forces off a guest or detaches a live
host display. Remove network/firewall/services only after all guests are gone;
never flush unrelated host firewall rules or delete bases still in use.
