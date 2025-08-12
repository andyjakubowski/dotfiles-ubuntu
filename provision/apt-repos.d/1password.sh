#!/bin/sh
set -eu pipefail

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://downloads.1password.com/linux/keys/1password.asc \
  | gpg --dearmor \
  | sudo tee /etc/apt/keyrings/1password.gpg >/dev/null
sudo chmod 0644 /etc/apt/keyrings/1password.gpg

cat <<EOF | sudo tee /etc/apt/sources.list.d/1password.sources >/dev/null
Types: deb
URIs: https://downloads.1password.com/linux/debian/$(dpkg --print-architecture)
Suites: stable
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/1password.gpg
EOF

POLICY_DIR="/etc/debsig/policies/AC2D62742012EA22"
KEYRING_DIR="/usr/share/debsig/keyrings/AC2D62742012EA22"
POLICY_FILE="$POLICY_DIR/1password.pol"
KEY_FILE="$KEYRING_DIR/debsig.gpg"

sudo mkdir -p "$POLICY_DIR" "$KEYRING_DIR"

tmp_pol="$(mktemp)"
tmp_key="$(mktemp)"
trap 'rm -f "$tmp_pol" "$tmp_key"' EXIT

# Fetch latest
curl -fsSL https://downloads.1password.com/linux/debian/debsig/1password.pol -o "$tmp_pol"
curl -fsSL https://downloads.1password.com/linux/keys/1password.asc | gpg --dearmor > "$tmp_key"

# Install only if changed
if ! [ -f "$POLICY_FILE" ] || ! cmp -s "$tmp_pol" "$POLICY_FILE"; then
  sudo install -m 0644 "$tmp_pol" "$POLICY_FILE"
fi

if ! [ -f "$KEY_FILE" ] || ! cmp -s "$tmp_key" "$KEY_FILE"; then
  sudo install -m 0644 "$tmp_key" "$KEY_FILE"
fi