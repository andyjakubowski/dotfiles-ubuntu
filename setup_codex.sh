#!/bin/sh
set -eu

# The official installer reuses complete releases and updates to latest by default.
# Set CODEX_RELEASE to a specific version to pin the desired release.
installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

curl -fsSL https://chatgpt.com/codex/install.sh -o "$installer"

# Dotfiles already manage PATH; prevent the installer from editing shell profiles.
PATH="$HOME/.local/bin:$PATH" \
CODEX_INSTALL_DIR="$HOME/.local/bin" \
CODEX_NON_INTERACTIVE=1 \
sh "$installer"
