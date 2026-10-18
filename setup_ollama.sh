#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Refuse to put model data on the Ubuntu disk if the data SSD is absent.
if ! mountpoint -q /mnt/data; then
    echo "Mount the data SSD at /mnt/data before setting up Ollama." >&2
    exit 1
fi

# Install once; subsequent dotfiles runs only reconcile configuration.
if ! command -v ollama >/dev/null 2>&1; then
    installer=$(mktemp)
    trap 'rm -f "$installer"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    curl -fsSL https://ollama.com/install.sh -o "$installer"
    sh "$installer"
fi

sudo install -d -o ollama -g ollama -m 0755 /mnt/data/ollama /mnt/data/ollama/models
sudo install -d -m 0755 /etc/systemd/system/ollama.service.d

source_config="$script_dir/provision/ollama/override.conf"
target_config=/etc/systemd/system/ollama.service.d/override.conf
config_changed=false
if ! sudo cmp -s "$source_config" "$target_config"; then
    sudo install -m 0644 "$source_config" "$target_config"
    config_changed=true
fi

sudo systemctl daemon-reload
sudo systemctl enable ollama.service
if [ "$config_changed" = true ]; then
    sudo systemctl restart ollama.service
else
    sudo systemctl start ollama.service
fi
