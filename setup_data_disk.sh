#!/bin/sh
set -eu pipefail

# ==== CONFIG ====
DEV="${DEV:-/dev/nvme0n1}"          # your 4TB SSD device
PART="${PART:-${DEV}p1}"            # first partition
MNT="${MNT:-/mnt/data}"             # mountpoint
FS="${FS:-ext4}"                    # filesystem
OWNER_USER="${OWNER_USER:-$USER}"   # owner of mount
OWNER_GROUP="${OWNER_GROUP:-$USER}" # group owner
# ===============

req() { command -v "$1" >/dev/null 2>&1 || { echo "Missing $1"; exit 1; }; }
req lsblk; req blkid; req grep; req sed; req awk; req sudo

# 0) Sanity checks
if [ ! -b "$DEV" ]; then
  echo "Block device $DEV not found"; exit 1
fi

# 1) Create partition table + partition if missing
if ! lsblk -no NAME "$DEV" | grep -q "$(basename "$PART")"; then
  echo "Creating GPT and primary partition on $DEV..."
  sudo parted -s "$DEV" mklabel gpt
  sudo parted -s -a opt "$DEV" mkpart primary "$FS" 0% 100%
  # Let kernel re-read partition table
  sudo partprobe "$DEV" || true
fi

# 2) Make filesystem if missing
FSTYPE="$(lsblk -no FSTYPE "$PART" || true)"
if [ -z "$FSTYPE" ]; then
  echo "Formatting $PART as $FS..."
  sudo mkfs."$FS" -F "$PART"
fi

# 3) Ensure mountpoint exists
if [ ! -d "$MNT" ]; then
  sudo mkdir -p "$MNT"
fi

# 4) Add/update /etc/fstab with UUID (idempotent)
UUID="$(blkid -s UUID -o value "$PART")"
FSTAB_LINE="UUID=$UUID  $MNT  $FS  defaults  0  2"

if ! grep -qs "UUID=$UUID" /etc/fstab; then
  echo "Adding entry to /etc/fstab..."
  echo "$FSTAB_LINE" | sudo tee -a /etc/fstab >/dev/null
else
  # If UUID present but mountpoint/options differ, replace line
  CURRENT="$(grep -s "UUID=$UUID" /etc/fstab | tail -n1)"
  if [ "$CURRENT" != "$FSTAB_LINE" ]; then
    echo "Updating existing /etc/fstab entry..."
    sudo sed -i "s|^UUID=$UUID .*|$FSTAB_LINE|" /etc/fstab
  fi
fi

# 5) Mount if not mounted
if ! mountpoint -q "$MNT"; then
  echo "Mounting $MNT..."
  sudo mount "$MNT"
fi

# 6) Ownership (idempotent)
sudo chown "$OWNER_USER:$OWNER_GROUP" "$MNT"

echo "Done. $PART mounted at $MNT (UUID=$UUID)."
