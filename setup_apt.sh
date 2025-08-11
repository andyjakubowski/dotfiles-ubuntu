#!/bin/sh
set -eu pipefail

# Add/refresh all vendor repos (keys + source repos)
repo_dir="$(dirname "$0")/provision/apt-repos.d"
if [ -d "$repo_dir" ]; then
  for f in "$repo_dir"/*.sh; do
    [ -e "$f" ] || continue
    sh "$f"
  done
fi

# Update apt and install packages from apt-packages.txt (ignoring comments/blank lines)
sudo apt-get update -y

pkg_file="$(dirname "$0")/apt-packages.txt"
if [ -f "$pkg_file" ]; then
  # strip comments etc.
  pkgs="$(grep -vE '^\s*($|#)' "$pkg_file" || true)"
  if [ -n "$pkgs" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $pkgs
  fi
fi


# Enable LFS filters globally
git lfs install --system || git lfs install


