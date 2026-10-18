#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 && $# == 1 ]] || { echo 'Run via setup_codex_vm.sh.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config=$1
fail() { echo "$*" >&2; exit 1; }
v() { virsh --connect qemu:///system "$@"; }
exec 9>/run/lock/dotfiles-codex-vm.lock
flock -n 9 || fail 'Another VM operation is running.'
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || fail 'Requires Ubuntu 24.04 x86_64.'
[[ -c /dev/kvm ]] || fail 'Missing /dev/kvm; enable CPU virtualization first.'
python3 -c 'import os,fcntl; f=os.open("/dev/kvm",os.O_RDWR); assert fcntl.ioctl(f,0xAE00,0)==12; os.close(f)'
python3 "$source_dir/vm.py" check-config "$config"
# Refuse to replace the previous draft's unmanaged fixed guest or networks.
if command -v virsh >/dev/null; then
    if v dominfo codex-vm >/dev/null 2>&1 && [[ ! -f /var/lib/codex-vm/instances/codex-vm/instance.json ]]; then
        fail 'Previous/unmanaged codex-vm exists. Plan migration explicitly; no disks are replaced.'
    fi
    if v net-info codex-vm >/dev/null 2>&1 && [[ ! -f /var/lib/codex-vm/lifecycle-v2 ]]; then
        fail 'Existing network is not managed by this lifecycle setup; review a migration first.'
    fi
fi
if [[ -f /var/lib/codex-vm/network-template.xml ]]; then
    cmp -s "$source_dir/network.xml" /var/lib/codex-vm/network-template.xml || \
        fail 'Network template changed. Review migration before changing an existing network.'
fi
python3 - <<'PY'
import ipaddress, json, subprocess
network = ipaddress.ip_network('192.168.250.0/24')
for route in json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'show', 'table', 'all'])):
    dest = route.get('dst', 'default')
    if dest != 'default' and route.get('dev') != 'virbr-codex' and network.overlaps(ipaddress.ip_network(dest, strict=False)):
        raise SystemExit('Guest subnet conflicts with existing route: ' + dest)
PY
mapfile -t packages < "$source_dir/apt-packages.txt"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
install -d -m 0755 /etc/codex-vm /usr/local/lib/codex-vm /var/lib/codex-vm/instances \
    /var/lib/libvirt/images/codex-vm/instances /var/lib/libvirt/images/codex-vm/bases \
    /etc/systemd/system/libvirtd.service.d
# Root-owned helper and data, never an executable link into the writable checkout.
for file in vm.py render.py guest.sh; do
    if ! cmp -s "$source_dir/$file" "/usr/local/lib/codex-vm/$file"; then
        install -m 0644 "$source_dir/$file" "/usr/local/lib/codex-vm/$file"
    fi
done
if ! cmp -s "$config" /etc/codex-vm/config.json; then
    install -m 0644 "$config" /etc/codex-vm/config.json
fi
launcher=$(mktemp)
trap 'rm -f "$launcher"' EXIT
printf '#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/codex-vm/vm.py "$@"\n' > "$launcher"
if ! cmp -s "$launcher" /usr/local/sbin/codex-vm; then
    install -m 0755 "$launcher" /usr/local/sbin/codex-vm
fi
# Validate the source before replacing the on-disk firewall. Never flush unrelated rules.
nft --check --file "$source_dir/firewall.nft"
install -m 0644 "$source_dir/firewall.nft" /etc/codex-vm/firewall.nft
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
    ip link show virbr-codex >/dev/null 2>&1 && fail 'Unmanaged bridge exists.'
    touch /var/lib/codex-vm/lifecycle-v2
    v net-define "$source_dir/network.xml"
    install -m 0644 "$source_dir/network.xml" /var/lib/codex-vm/network-template.xml
fi
v net-autostart codex-vm
if ! v net-list --name | grep -qx codex-vm; then v net-start codex-vm; fi
printf 'Host infrastructure ready. No guest created, no GPU driver detached, no reboot performed.\n'
printf 'Next: sudo codex-vm inspect. See docs/codex-vm.md for GPU preparation and lifecycle commands.\n'
