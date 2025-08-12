#!/bin/sh
set -eu pipefail

# Set zsh as default shell
chsh -s $(which zsh)

# Hugging Face cache on large SSD
if [ -d /mnt/data ]; then
    export HF_HOME=/mnt/data/hf_cache
    sudo mkdir -p "$HF_HOME"
    sudo chown -R "$USER":"$USER" "$HF_HOME"
else
    echo "Warning: /mnt/data not found — HF_HOME not set." >&2
fi


