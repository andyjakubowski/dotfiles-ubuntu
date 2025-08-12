#!/bin/sh
set -eu pipefail

# Fast keyboard repeat rate

# Lower delay before repeat (default is ~500 ms)
gsettings set org.gnome.desktop.peripherals.keyboard delay 200

# Increase repeat speed (default is ~25)
gsettings set org.gnome.desktop.peripherals.keyboard repeat-interval 5
