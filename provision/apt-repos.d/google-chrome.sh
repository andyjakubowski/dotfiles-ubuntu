#!/bin/sh
set -eu pipefail

sudo install -m 0755 -d /etc/apt/keyrings

curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor | sudo tee /etc/apt/keyrings/google.gpg > /dev/null
sudo chmod 0644 /etc/apt/keyrings/google.gpg

list=/etc/apt/sources.list.d/google-chrome.list
arch="$(dpkg --print-architecture)"
line="deb [arch=${arch} signed-by=/etc/apt/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main"

if [ ! -f "$list" ] || ! grep -qxF "$line" "$list"; then
  echo "$line" | sudo tee "$list" >/dev/null
fi

