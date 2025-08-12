#!/bin/sh
set -eu pipefail

# 1password might have added a new keyring or source list entry. Remove it to avoid duplicates, we did this manually ourselves already.
sudo rm -f /etc/apt/sources.list.d/1password.list