# Optional isolated Codex VM

**Status:** these are the current single-VM scripts, not the planned disposable
GPU workstation. GPU passthrough, model sharing and disposable clones are not
implemented. Start with [the architecture and developer workflow](gpu-vm-explained.md)
to understand the intended setup; this guide documents the existing scripts.

This setup targets Ubuntu 24.04 x86_64 with working KVM. It follows
[Ubuntu's libvirt guidance](https://ubuntu.com/server/docs/how-to/virtualisation/libvirt/)
and uses an official Ubuntu cloud image with cloud-init. OpenAI recognizes a
[dedicated sandbox VM](https://learn.chatgpt.com/docs/developer-commands) as an
appropriate environment for bypassing Codex's internal sandbox and approvals;
it does not provide this particular Ubuntu provisioning implementation.

## Review before running

Ordinary `./install` runs skip VM setup. Enable it explicitly:

```sh
DOTFILES_CODEX_VM=1 CODEX_VM_SSH_PUBLIC_KEY=/absolute/path/to/client-key.pub ./setup_codex_vm.sh
```

Or set the same variables when running `./install` to include the VM in the
full dotfiles setup. The public key must belong to the client you will connect
from (for example your Mac). Copy only that public key onto ai-box; its private
key stays on your client. No key generation, agent forwarding, or credential
copying is performed. Missing/invalid public keys fail before sudo.
The wrapper requires Python 3 and `ssh-keygen` (Ubuntu's `openssh-client`).

Defaults in `provision/codex-vm/config.sh` are 8 vCPUs, 16384 MiB RAM and a
100 GiB sparse disk. Override with `CODEX_VM_CPUS`, `CODEX_VM_MEMORY_MIB`, and
`CODEX_VM_DISK_GIB`. Capacity is checked before creating the disk. All VM data
is on the system disk under `/var/lib/libvirt/images/codex-vm`; `/mnt/data`
and physical disks/partitions are not modified. Sparse disks can grow to their
configured capacity; monitor host free space. A later machine uses these same
defaults unless you record different defaults in the repository.

### Host effects

- Installs packages from `provision/codex-vm/apt-packages.txt` and their dependencies
  through Ubuntu APT; refreshes the APT index. Package installation may start
  libvirt and its default network before the custom network is defined.
- Uses existing KVM kernel modules. Does not replace the kernel, change UEFI,
  relax AppArmor, or repartition storage. Package services may load modules.
- Enables `libvirtd.service` and `codex-vm-firewall.service`, installing the
  latter and a libvirtd ordering dependency under `/etc/systemd/system`.
- Installs `/etc/codex-vm/firewall.nft`. It atomically replaces only the
  `inet codex_vm` table, never the global firewall ruleset. Does not enable or
  restart the generic `nftables.service` or rewrite `/etc/nftables.conf`.
- Creates a libvirt NAT network `codex-vm`, bridge `virbr-codex`, subnet
  `192.168.250.0/24`, and a DHCP/DNS process. Libvirt enables IP forwarding and
  manages its own NAT/firewall rules. Existing routes overlapping this subnet
  cause setup to stop. No changes to NetworkManager profiles or physical links.
- Autostarts the network at boot, after the custom firewall. The VM does not
  autostart. Keep the firewall service and libvirtd dependency installed while
  the VM exists. Global firewall flushes or other administrators can remove
  this boundary; this is not protection against a compromised host.
- Stores image downloads in `/var/cache/codex-vm`, creation metadata and
  cloud-init inputs in `/var/lib/codex-vm`, and the disk plus seed ISO in
  `/var/lib/libvirt/images/codex-vm`. None belongs in Git. Downloads are verified
  against Canonical's SHA256SUMS fetched over HTTPS. The initial image and Codex
  installer track current releases, so fresh builds are repeatable procedures,
  not byte-identical builds. The downloaded image checksums are retained.
- Does not add your user to `kvm`/`libvirt`, change host sudo rules, or pass host
  credentials into the VM. VM management uses explicit host `sudo virsh`.

The boundary permits guest IPv4 internet egress, DNS/DHCP to the bridge, and
responses to host-initiated connections. New guest connections to host services
are dropped. Forwarded traffic to private, loopback, link-local, Tailscale CGNAT,
multicast and reserved IPv4 ranges is dropped; guest IPv6 forwarding is dropped.
Public IP destinations are allowed, including public servers you may own.
Existing host firewalls may further restrict permitted traffic. DNS queries are
forwarded by libvirt; the guest cannot directly use arbitrary host services.

The guest account `codex` has passwordless **guest-only sudo** and key-only SSH.
There are no host filesystem shares, GPU/device passthrough, Docker sockets,
host SSH agents or VM guest-agent channel. Default guest packages include Git,
Python/venv, build tools, OpenSSH and Codex CLI. Codex authentication and enabling
unrestricted execution remain explicit follow-up steps.

## Finish and verify before full access

Cloud-init continues after the host script exits. On the client, add:

```sshconfig
Host codex-vm
    HostName 192.168.250.2
    User codex
    ProxyJump ai-box
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ForwardAgent no
```

Use your actual existing `ai-box` alias and client key path. ProxyJump transports
SSH without forwarding your agent or copying its private key. No guest SSH port
is exposed on the physical host interfaces. The jump user's SSH server must
permit TCP forwarding.

```sh
ssh codex-vm 'cloud-init status --wait'
ssh codex-vm 'test -f /var/lib/codex-vm-provisioned && ~/.local/bin/codex --version'
ssh codex-vm 'hostname; lsblk; findmnt -t virtiofs,9p'
```

`findmnt` should find no shared filesystems (exit 1 is expected). Inspect host
`sudo virsh --connect qemu:///system dumpxml codex-vm` for the intended disk and
single network, with no filesystem, hostdev, or channel entries. Inspect
`sudo nft list table inet codex_vm` and ensure the firewall service is enabled.
From the guest, test a public HTTPS request works and TCP connections to an
actual listening host/LAN service time out; use bounded connection timeouts.
Verify both host bridge and physical host IPs, LAN and Tailscale addresses, and
IPv6. Repeat the boundary checks after a host reboot before unattended use.
A timeout alone is not proof: confirm the relevant firewall drop counter rises.

If cloud-init failed, inspect `sudo cat /var/log/cloud-init-output.log` inside
the guest. Resolve the failure there or explicitly plan a rebuild. Rerunning the
host installer preserves the disk and does not rerun cloud-init or erase data.

Authenticate from an interactive guest session with `~/.local/bin/codex login`.
Use the supported login flow it offers. See OpenAI's
[SSH connection instructions](https://learn.chatgpt.com/docs/remote-connections#connect-to-an-ssh-host):
add/enable `codex-vm` in the desktop app's SSH connections and select
`/home/codex/workspace`. Confirm `codex` is available in the guest login shell.

Only after checking the boundary, run inside the guest:

```sh
codex --sandbox danger-full-access --ask-for-approval never
```

Do not put these settings in the host's Codex config. Everything readable in the
guest, including Codex authentication credentials, is available to guest code.

## Reruns and changes

Rerunning with the same public key and configuration reconciles host packages,
firewall and services. It preserves an existing VM's disk and power state; it
does not recreate or resize it. Manifest changes (resources, SSH key or guest
provisioning) fail before package installation rather than silently changing an
existing guest. Plan those migrations explicitly. An existing unmanaged domain,
network or orphan disk is never adopted or overwritten automatically. Failures
can leave partial setup; review the reported state rather than deleting disks.

## Start, stop, and baseline backup

On ai-box:

```sh
sudo virsh --connect qemu:///system start codex-vm
sudo virsh --connect qemu:///system shutdown codex-vm
sudo virsh --connect qemu:///system domstate codex-vm
```

For a baseline, finish provisioning and verification, then shut down and wait
until `domstate` reports `shut off`. Make an offline disk copy (never copy a
running guest's disk):

```sh
sudo cp --reflink=auto --sparse=always --preserve=all --no-clobber \
  /var/lib/libvirt/images/codex-vm/root.qcow2 \
  /var/lib/libvirt/images/codex-vm/baseline.qcow2
```

This is a disk backup, not a live memory snapshot. It needs additional storage
and includes any credentials already in the guest. To restore, shut down and
verify the guest is off, preserve the current disk separately, then copy the
baseline back with its original ownership/permissions before starting. Restoration
discards later guest changes; do it only as an explicit separate operation.

Removal is deliberately manual: shut down and undefine the domain, destroy and
undefine its network, then remove only its dedicated firewall/service/config
and data after reviewing what to retain. Keep the boundary until the guest is
gone. Do not purge shared virtualization packages or flush unrelated firewall rules.
