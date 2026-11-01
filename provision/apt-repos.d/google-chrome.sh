#!/bin/sh
set -eu

# Finish the unprivileged download and conversion before replacing a trusted key.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub -o "$work/key.asc"
gpg --batch --yes --dearmor --output "$work/key.gpg" "$work/key.asc"
sudo install -m 0755 -d /etc/apt/keyrings
sudo install -m 0644 "$work/key.gpg" /etc/apt/keyrings/google.gpg

list=/etc/apt/sources.list.d/google-chrome.list
arch="$(dpkg --print-architecture)"
line="deb [arch=${arch} signed-by=/etc/apt/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main"

if [ ! -f "$list" ] || ! grep -qxF "$line" "$list"; then
  echo "$line" | sudo tee "$list" >/dev/null
fi


