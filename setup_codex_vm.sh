#!/usr/bin/env bash
set -euo pipefail
if [[ ${DOTFILES_CODEX_VM:-0} != 1 ]]; then
    echo 'Skipping optional Codex GPU VM infrastructure (enable with DOTFILES_CODEX_VM=1).'
    exit 0
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/provision/codex-vm/config.sh"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
python3 "$script_dir/provision/codex-vm/vm.py" configure \
    --key "$CODEX_VM_SSH_PUBLIC_KEY" --cpus "$CODEX_VM_CPUS" \
    --memory "$CODEX_VM_MEMORY_MIB" --disk "$CODEX_VM_DISK_GIB" \
    --nvidia-package "$CODEX_VM_NVIDIA_PACKAGE" --gpu "$CODEX_VM_GPU_BDF" \
    --output "$work/config.json"
sudo bash "$script_dir/provision/codex-vm/host.sh" "$work/config.json"
