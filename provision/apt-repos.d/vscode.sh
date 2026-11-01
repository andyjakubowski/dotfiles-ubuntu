#!/bin/sh
set -eu

# Finish the unprivileged download and conversion before replacing a trusted key.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o "$work/key.asc"
gpg --batch --yes --dearmor --output "$work/key.gpg" "$work/key.asc"
sudo install -m 0755 -d /etc/apt/keyrings
sudo install -m 0644 "$work/key.gpg" /etc/apt/keyrings/microsoft.gpg

cat <<EOF | sudo tee /etc/apt/sources.list.d/vscode.sources >/dev/null
Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/microsoft.gpg
EOF

