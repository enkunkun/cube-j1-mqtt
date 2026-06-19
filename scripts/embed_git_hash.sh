#!/usr/bin/env bash
# Rewrite BRIDGE_GIT_HASH in production_tool/mqtt_bridge.py with the commit
# that was actually pushed to GitHub fork (`enkunkun/cube-j1-mqtt`).
#
# Two gates run before the rewrite:
#   1. production_tool/mqtt_bridge.py must be free of uncommitted changes —
#      otherwise the deployed bridge would silently disagree with the hash
#      it reports.
#   2. `gh api repos/enkunkun/cube-j1-mqtt/commits/main --jq '.sha[:7]'` must
#      succeed so the hash is taken from the **remote main**, not whichever
#      stale commit the local git HEAD happens to point at (jj colocated
#      workspaces routinely rewrite local commits without moving git HEAD).
#
# Escape hatches:
#   - `ALLOW_UNCOMMITTED=1` skips both gates and stamps "unknown". Useful
#     for offline / no-gh builds during development.
#
# Exit codes:
#   0  success
#   1  gate failed or gh unavailable
set -euo pipefail

cd "$(dirname "$0")/.."

TARGET="production_tool/mqtt_bridge.py"

if [ ! -f "$TARGET" ]; then
    echo "embed_git_hash.sh: $TARGET not found" >&2
    exit 1
fi

if [ "${ALLOW_UNCOMMITTED:-0}" = "1" ]; then
    sed -i.bak 's|^BRIDGE_GIT_HASH = .*|BRIDGE_GIT_HASH = "unknown"|' "$TARGET"
    rm -f "${TARGET}.bak"
    echo "embed_git_hash.sh: ALLOW_UNCOMMITTED=1 — stamped 'unknown' (gates skipped)"
    exit 0
fi

# Gate 1: production_tool/mqtt_bridge.py must match what is published on the
# fork's `main` branch. Comparing against local git HEAD is not enough under
# jj colocated workflows because jj routinely rewrites local commits without
# moving git HEAD. Comparing against `fork/main` (the remote-tracking ref)
# guarantees "what we are about to ship == what is on GitHub".
REMOTE_REF="fork/main"
if ! git rev-parse --verify --quiet "${REMOTE_REF}" >/dev/null; then
    cat >&2 <<EOF
embed_git_hash.sh: remote-tracking ref '${REMOTE_REF}' is missing.

Run 'jj git fetch --remote fork' (or 'git fetch fork') so the script can
verify that ${TARGET} matches what is published on GitHub. Or set
ALLOW_UNCOMMITTED=1 to bypass for offline builds.
EOF
    exit 1
fi
if ! git diff --quiet "${REMOTE_REF}" -- "$TARGET" 2>/dev/null; then
    cat >&2 <<EOF
embed_git_hash.sh: $TARGET differs from ${REMOTE_REF}.

The bridge would lie about its version if we deployed this build. Commit
& push the changes before deploying, or set ALLOW_UNCOMMITTED=1 to bypass
for local experiments.

Inspect with:
  git diff ${REMOTE_REF} -- $TARGET
EOF
    exit 1
fi

# Gate 2: derive the hash from the GitHub fork's main bookmark — the single
# source of truth for "what has been published". This is more correct than
# `git rev-parse --short HEAD` under jj colocated workflows, where the local
# HEAD is often a rewrite of an already-pushed commit.
HASH="$(gh api repos/enkunkun/cube-j1-mqtt/commits/main --jq '.sha[:7]' 2>/dev/null || true)"
if [ -z "$HASH" ]; then
    cat >&2 <<EOF
embed_git_hash.sh: could not resolve enkunkun/cube-j1-mqtt main sha via gh.

Make sure 'gh auth status' shows you are signed in, or set
ALLOW_UNCOMMITTED=1 to fall back to 'unknown' for offline builds.
EOF
    exit 1
fi

sed -i.bak "s|^BRIDGE_GIT_HASH = .*|BRIDGE_GIT_HASH = \"${HASH}\"|" "$TARGET"
rm -f "${TARGET}.bak"

echo "BRIDGE_GIT_HASH set to ${HASH} in ${TARGET} (from enkunkun/cube-j1-mqtt main)"
