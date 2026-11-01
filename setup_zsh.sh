#!/bin/sh
set -eu

# Set zsh as default shell
zsh_path=$(command -v zsh) || {
    echo "Install zsh before running setup_zsh.sh." >&2
    exit 1
}
if [ "${SHELL:-}" != "$zsh_path" ]; then
    chsh -s "$zsh_path"
fi

# Hugging Face cache on large SSD
HF_HOME=${HF_HOME:-/mnt/data/hf_cache}
if mountpoint -q /mnt/data; then
    owner_user=${USER:-$(id -un)}
    owner_group=$(id -gn "$owner_user")
    sudo install -d -o "$owner_user" -g "$owner_group" "$HF_HOME"
else
    echo "Warning: /mnt/data is not mounted; skipping Hugging Face cache setup." >&2
fi



