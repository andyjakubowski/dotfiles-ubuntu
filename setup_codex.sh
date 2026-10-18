#!/bin/sh
set -eu

# Ubuntu 24.04 needs the supplied Bubblewrap exception to create user namespaces.
# Keep the system-wide AppArmor restriction enabled.
# https://developers.openai.com/codex/concepts/sandboxing#prerequisites
. /etc/os-release
if [ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ]; then
  profile_source=/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
  profile_target=/etc/apparmor.d/bwrap-userns-restrict
  if [ ! -f "$profile_source" ]; then
    echo "Missing $profile_source; run ./setup_apt.sh first." >&2
    exit 1
  fi
  sudo install -m 0644 "$profile_source" "$profile_target"
  sudo apparmor_parser -r "$profile_target"
fi

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
