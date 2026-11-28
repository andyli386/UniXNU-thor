#!/bin/bash
# The whole pipeline under a sandbox that denies every access to the old project directories: proves that the build
# needs nothing from them.   thor/sandbox-check.sh [name of the working copy, default sandbox-check]
set -u
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
name=${1:-sandbox-check}
deny=""; for d in /Users/*/projects/thor-unixnu* /Users/*/projects/thor-uefi-publish-*; do [ -d "$d" ] && deny="$deny(subpath \"$d\")"; done
profile="(version 1)(allow default)(deny file-read* $deny)"
for d in /Users/*/projects/thor-unixnu*; do
  /usr/bin/sandbox-exec -p "$profile" /bin/ls "$d" >/dev/null 2>&1 && { echo "the sandbox does not deny $d" >&2; exit 9; }
done
/usr/bin/sandbox-exec -p "$profile" /bin/ls "$THOR_PONG/scripts" >/dev/null || { echo "the sandbox denies the new directory" >&2; exit 9; }
/usr/bin/sandbox-exec -p "$profile" "$THOR_PY" -I "$THOR_REPO/thor/bootstrap.py" "$THOR_REPO/thor/versions/v247.json" --name "$name" > "$THOR_WORK/$name.log.bootstrap" 2>&1; echo "bootstrap exit $?"
/usr/bin/sandbox-exec -p "$profile" "$THOR_REPO/thor/build-kernel.sh" "$THOR_WORK/$name" > "$THOR_WORK/$name.log.build" 2>&1; echo "kernel build exit $?"
/usr/bin/sandbox-exec -p "$profile" "$THOR_PY" -I "$THOR_REPO/thor/package.py" "$THOR_REPO/thor/versions/v247.json" --work "$THOR_WORK/$name" > "$THOR_WORK/$name.log.package" 2>&1; echo "package exit $?"
echo "end $(date '+%F %T')"
