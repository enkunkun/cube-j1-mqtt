#!/usr/bin/env bash
# Stage the USB stick for a Cube J1 boot.
#
# Usage:
#   scripts/prepare_usb.sh <usb_mount_path>
#
# Steps:
#   1. Verify <usb_mount_path> is a writable directory.
#   2. Run scripts/embed_git_hash.sh to stamp the current git short hash
#      into production_tool/mqtt_bridge.py.
#   3. Copy CubeJMTS.txt and production_tool/ to the USB root.
#   4. If secrets/wpa_supplicant.conf exists, overlay it onto the USB
#      (otherwise leave the in-tree template — Cube J1 won't join Wi-Fi).
#   5. If secrets/config.json exists, overlay it onto the USB.
#   6. Restore the in-tree mqtt_bridge.py BRIDGE_GIT_HASH back to "unknown"
#      so the working copy stays clean.
#
# The secrets/ directory is .gitignored. Put real credentials there:
#   secrets/wpa_supplicant.conf  -- real Wi-Fi SSID + PSK
#   secrets/config.json          -- real B-route ID/pwd and MQTT creds
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $0 <usb_mount_path>" >&2
    exit 2
fi

USB="$1"
cd "$(dirname "$0")/.."

if [ ! -d "$USB" ]; then
    echo "prepare_usb.sh: $USB is not a directory" >&2
    exit 1
fi
if [ ! -w "$USB" ]; then
    echo "prepare_usb.sh: $USB is not writable" >&2
    exit 1
fi

echo "[1/6] Embedding git short hash into production_tool/mqtt_bridge.py"
./scripts/embed_git_hash.sh

echo "[2/6] Copying CubeJMTS.txt"
cp CubeJMTS.txt "$USB/"

echo "[3/6] Copying production_tool/"
# Use -R so a freshly-formatted USB also gets the directory created.
cp -R production_tool "$USB/"

echo "[4/6] Overlaying secrets/wpa_supplicant.conf (if present)"
if [ -f secrets/wpa_supplicant.conf ]; then
    cp secrets/wpa_supplicant.conf "$USB/production_tool/wpa_supplicant.conf"
    echo "       overlaid real Wi-Fi credentials"
else
    echo "       secrets/wpa_supplicant.conf not found - using template (no Wi-Fi)"
fi

echo "[5/6] Overlaying secrets/config.json (if present)"
if [ -f secrets/config.json ]; then
    cp secrets/config.json "$USB/production_tool/config.json"
    echo "       overlaid real B-route + MQTT credentials"
else
    echo "       secrets/config.json not found - using template (bridge won't authenticate)"
fi

echo "[6/6] Restoring in-tree BRIDGE_GIT_HASH to 'unknown'"
sed -i.bak 's|^BRIDGE_GIT_HASH = .*|BRIDGE_GIT_HASH = "unknown"|' production_tool/mqtt_bridge.py
rm -f production_tool/mqtt_bridge.py.bak

echo
echo "Done. Eject the USB with:"
echo "   diskutil unmountDisk <disk-identifier>"
echo "or, on Linux:"
echo "   umount $USB"
