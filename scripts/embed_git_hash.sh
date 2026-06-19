#!/usr/bin/env bash
# Rewrite BRIDGE_GIT_HASH in production_tool/mqtt_bridge.py with the current
# git short hash. Run this BEFORE copying production_tool/ onto the USB stick.
# If git is unavailable or HEAD is not resolvable, the value falls back to
# "unknown" (matches the in-tree default).
set -euo pipefail

cd "$(dirname "$0")/.."

HASH="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
TARGET="production_tool/mqtt_bridge.py"

if [ ! -f "$TARGET" ]; then
    echo "embed_git_hash.sh: $TARGET not found" >&2
    exit 1
fi

# Use sed -i with .bak suffix for macOS/BSD compatibility, then remove the bak.
sed -i.bak "s|^BRIDGE_GIT_HASH = .*|BRIDGE_GIT_HASH = \"${HASH}\"|" "$TARGET"
rm -f "${TARGET}.bak"

echo "BRIDGE_GIT_HASH set to ${HASH} in ${TARGET}"
