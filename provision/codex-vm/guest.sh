#!/bin/sh
set -eu
# This marker exists only in our guest seed, not on the host.
test -f /etc/codex-vm-guest
nvidia_package=$(cat /etc/codex-vm-nvidia-package)
case "$nvidia_package" in nvidia-driver-*-open) ;; *) exit 1 ;; esac
# Clones retain installed tools; only fresh images need the installation work.
if ! dpkg-query -W -f='${Status}' "$nvidia_package" 2>/dev/null | grep -q 'install ok installed'; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates curl git build-essential python3 python3-venv openssh-server \
        linux-generic "$nvidia_package"
fi
install -d -o codex -g codex -m 0755 /home/codex/.local/bin /home/codex/workspace
if ! test -x /home/codex/.local/bin/codex; then
    installer=$(mktemp)
    trap 'rm -f "$installer"' EXIT
    curl -fsSL https://chatgpt.com/codex/install.sh -o "$installer"
    chmod 0644 "$installer"
    runuser -u codex -- env HOME=/home/codex CODEX_INSTALL_DIR=/home/codex/.local/bin \
        CODEX_NON_INTERACTIVE=1 sh "$installer"
fi
install -d -o codex -g codex -m 0700 /home/codex/.codex
# These settings exist only inside the isolated guest, never in host config.
printf 'sandbox_mode = "danger-full-access"\napproval_policy = "never"\n' > /home/codex/.codex/config.toml
chown codex:codex /home/codex/.codex/config.toml
chmod 0600 /home/codex/.codex/config.toml
/home/codex/.local/bin/codex --version
findmnt -n -T /mnt/data -o FSTYPE | grep -qx 9p
findmnt -n -T /mnt/data -o OPTIONS | tr ',' '\n' | grep -qx ro
touch /var/lib/codex-vm-provisioned
printf 'Provisioned. Reboot this guest before verifying nvidia-smi (the installed kernel may be newer).\n'
