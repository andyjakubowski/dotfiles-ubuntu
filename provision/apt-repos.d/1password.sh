#!/bin/sh
set -eu

# Prepare both APT and debsig inputs before replacing any trusted configuration.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
curl -fsSL https://downloads.1password.com/linux/keys/1password.asc -o "$work/key.asc"
gpg --batch --yes --dearmor --output "$work/key.gpg" "$work/key.asc"
curl -fsSL https://downloads.1password.com/linux/debian/debsig/1password.pol -o "$work/policy"

POLICY_DIR="/etc/debsig/policies/AC2D62742012EA22"
KEYRING_DIR="/usr/share/debsig/keyrings/AC2D62742012EA22"
POLICY_FILE="$POLICY_DIR/1password.pol"
KEY_FILE="$KEYRING_DIR/debsig.gpg"

sudo install -m 0755 -d /etc/apt/keyrings "$POLICY_DIR" "$KEYRING_DIR"
sudo install -m 0644 "$work/key.gpg" /etc/apt/keyrings/1password.gpg

# Install only if changed. Reuse the same validated key for debsig.
if ! [ -f "$POLICY_FILE" ] || ! cmp -s "$work/policy" "$POLICY_FILE"; then
  sudo install -m 0644 "$work/policy" "$POLICY_FILE"
fi
if ! [ -f "$KEY_FILE" ] || ! cmp -s "$work/key.gpg" "$KEY_FILE"; then
  sudo install -m 0644 "$work/key.gpg" "$KEY_FILE"
fi

cat <<EOF | sudo tee /etc/apt/sources.list.d/1password.sources >/dev/null
Types: deb
URIs: https://downloads.1password.com/linux/debian/$(dpkg --print-architecture)
Suites: stable
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/1password.gpg
EOF
