#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Read-only look into one SHNGROOT region of the port from TWRP.
#   REGION=OS|SystemOS|Data shngvol.sh vols
#   REGION=OS VOL=<volume name or index> shngvol.sh ls|stat|extents <path>
#   REGION=OS VOL=<volume> shngvol.sh cat <path> <local file> [<offset> <length>]
out=${THOR_ROOT}/thor-out
exec /opt/homebrew/bin/python3.13 -I "$out/session-tools/apfs_region.py" "$out/v228-intake/full-snapshot.py" \
  ${THOR_ROOT}/UniXNU-pong/tools/apfs "${REGION:-OS}" "$1" "${VOL:-0}" "${@:2}"
