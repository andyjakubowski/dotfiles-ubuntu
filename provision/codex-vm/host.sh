#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 && $# == 1 ]] || { echo 'Run via setup_codex_vm.sh.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
rendered=$1
state=/var/lib/codex-vm
images=/var/lib/libvirt/images/codex-vm
v() { virsh --connect qemu:///system "$@"; }
fail() { echo "$*" >&2; exit 1; }

exec 9>/run/lock/dotfiles-codex-vm.lock
flock -n 9 || fail 'Another VM setup is already running.'
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || fail 'This setup targets Ubuntu 24.04 on x86_64.'
[[ -c /dev/kvm ]] || fail 'Missing /dev/kvm; enable virtualization before proceeding.'
python3 -c 'import os,fcntl; f=os.open("/dev/kvm",os.O_RDWR); assert fcntl.ioctl(f,0xAE00,0)==12; os.close(f)'

read -r cpus memory disk < <(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m["cpus"],m["memory_mib"],m["disk_gib"])' "$rendered/manifest.json")
[[ $cpus -le $(nproc) ]] || fail 'Requested vCPUs exceed available host CPUs.'

# Refuse immutable guest changes before installing packages or reconciling host state.
if [[ -f $state/manifest.json ]]; then
    cmp -s "$rendered/manifest.json" "$state/manifest.json" || fail 'Guest configuration changed. Existing disk is preserved; review an explicit migration or recreate plan.'
fi
existing_guest=false
if command -v virsh >/dev/null && v dominfo codex-vm >/dev/null 2>&1; then
    [[ -f $state/manifest.json ]] || fail 'Unmanaged codex-vm domain exists; refusing to adopt it.'
    existing_guest=true
fi
if [[ $existing_guest == false ]]; then
    [[ ! -e $images/root.qcow2 ]] || fail 'Orphan guest disk exists; refusing to overwrite it. Review recovery manually.'
    available_mib=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
    [[ $memory -lt $available_mib ]] || fail 'Insufficient available RAM for requested guest.'
fi
python3 - <<'PY'
import ipaddress, json, subprocess
target = ipaddress.ip_network('192.168.250.0/24')
routes = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'show', 'table', 'all']))
for route in routes:
    dest = route.get('dst', 'default')
    if dest != 'default' and route.get('dev') != 'virbr-codex' and target.overlaps(ipaddress.ip_network(dest, strict=False)):
        raise SystemExit('VM subnet overlaps an existing route: ' + dest)
PY

mapfile -t packages < "$source_dir/apt-packages.txt"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
install -d -m 0755 /etc/codex-vm /etc/systemd/system/libvirtd.service.d "$state" "$images"

if v net-info codex-vm >/dev/null 2>&1; then
    [[ -f $state/network.xml ]] || fail 'Unmanaged codex-vm network exists; refusing to adopt it.'
    cmp -s "$source_dir/network.xml" "$state/network.xml" || fail 'Network configuration changed; stop the guest and review a network migration first.'
elif ip link show virbr-codex >/dev/null 2>&1; then
    fail 'An unmanaged virbr-codex interface exists.'
fi

install -m 0644 "$source_dir/firewall.nft" /etc/codex-vm/firewall.nft
# Validate before applying; nft applies the replacement as one transaction.
nft --check --file /etc/codex-vm/firewall.nft
install -m 0644 "$source_dir/codex-vm-firewall.service" /etc/systemd/system/codex-vm-firewall.service
printf '[Unit]\nRequires=codex-vm-firewall.service\nAfter=codex-vm-firewall.service\n' > /etc/systemd/system/libvirtd.service.d/codex-vm.conf
systemctl daemon-reload
systemctl enable codex-vm-firewall.service libvirtd.service
if systemctl is-active --quiet codex-vm-firewall.service; then
    systemctl reload codex-vm-firewall.service
else
    systemctl start codex-vm-firewall.service
fi
systemctl start libvirtd.service

if ! v net-info codex-vm >/dev/null 2>&1; then
    v net-define "$source_dir/network.xml"
    install -m 0644 "$source_dir/network.xml" "$state/network.xml"
fi
v net-autostart codex-vm
if ! v net-list --name | grep -qx codex-vm; then v net-start codex-vm; fi

if v dominfo codex-vm >/dev/null 2>&1; then
    echo 'Existing guest preserved (including its current power state). Host boundary reconciled.'
    exit 0
fi
[[ ! -e $images/root.qcow2 ]] || fail 'Orphan guest disk exists; refusing to overwrite it. Review recovery manually.'
# Allow a fully allocated disk plus image/download overhead; never repartition storage.
available_gib=$(df -BG --output=avail "$images" | tail -1 | tr -dc '0-9')
[[ $available_gib -gt $((disk + 5)) ]] || fail 'Insufficient disk headroom.'
install -d -m 0755 /var/cache/codex-vm
cache=/var/cache/codex-vm
base_url=https://cloud-images.ubuntu.com/noble/current
image=noble-server-cloudimg-amd64.img
curl -fSL "$base_url/SHA256SUMS" -o "$cache/SHA256SUMS"
checksum=$(awk -v file="$image" '$2 == file || $2 == "*"file {print $1}' "$cache/SHA256SUMS")
[[ $checksum =~ ^[a-f0-9]{64}$ ]] || fail 'No unique image checksum found.'
if ! printf '%s  %s\n' "$checksum" "$cache/$image" | sha256sum --check --status; then
    curl -fSL "$base_url/$image" -o "$cache/$image.partial"
    printf '%s  %s\n' "$checksum" "$cache/$image.partial" | sha256sum --check
    mv "$cache/$image.partial" "$cache/$image"
fi
install -m 0644 "$rendered/user-data" "$state/user-data"
install -m 0644 "$rendered/meta-data" "$state/meta-data"
cloud-localds "$images/seed.iso" "$state/user-data" "$state/meta-data"
qemu-img convert -f qcow2 -O qcow2 "$cache/$image" "$images/root.qcow2"
qemu-img resize "$images/root.qcow2" "${disk}G"
chown libvirt-qemu:kvm "$images/root.qcow2" "$images/seed.iso"
chmod 0600 "$images/root.qcow2"
install -m 0644 "$rendered/manifest.json" "$state/manifest.json"
install -m 0644 "$cache/SHA256SUMS" "$state/image-SHA256SUMS"
virt-install --connect qemu:///system --name codex-vm --virt-type kvm \
    --memory "$memory" --vcpus "$cpus" --cpu host-passthrough \
    --import --os-variant ubuntu24.04 --graphics none --noautoconsole \
    --disk "path=$images/root.qcow2,format=qcow2,bus=virtio" \
    --disk "path=$images/seed.iso,device=cdrom" \
    --network network=codex-vm,model=virtio,mac=52:54:00:ca:de:01
# Deliberately no VM autostart, shared filesystems, agent sockets or passthrough.
echo 'VM started. Guest provisioning is asynchronous; follow docs/codex-vm.md to verify completion.'
