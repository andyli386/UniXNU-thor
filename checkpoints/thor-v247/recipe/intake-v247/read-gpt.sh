#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Read the first and the last blocks of the UFS LUNs raw, through TWRP's own storage driver (dd; nothing
# is written anywhere): the partition tables at both ends, to compare the primary and the backup GPT.
#   read-gpt.sh <destination directory> [adb] [serial]
# 2026-11-27: every boot from flash of v239, v241 and v242 fails the kernel's check of LUN 4 (the modem
# and Wi-Fi firmware LUN) because the primary and the backup GPT header name different CRCs for the
# partition entries.  This reads what is really there.
set -uo pipefail
dir=${1:?destination directory}
ad=${2:-/opt/homebrew/bin/adb}
serial=${3:-${THOR_SERIAL}}
mkdir -p "$dir"
S() { "$ad" -s "$serial" shell -n "$@" 2>/dev/null | tr -d '\r'; }
sizes=$(S 'for d in /sys/block/sd*; do echo "$(basename $d) $(cat $d/size) $(cat $d/queue/logical_block_size 2>/dev/null)"; done')
[ -n "$sizes" ] || { echo "no block devices listed"; exit 2; }
printf '%s\n' "$sizes" > "$dir/sizes.txt"
echo "$sizes" | sed 's/^/device /'
# name device number-of-4096-byte-blocks blocks-to-read-at-each-end
# The bytes travel as hex text through `adb shell`, the way the log dumper talks to TWRP, and are
# put back into binary here.
hexdump_of() { # dd-command (already filtered)
  S "( $1 status=none | { command -v od >/dev/null 2>&1 && od -An -v -tx1 || hexdump -ve '1/1 \"%02x\"'; } ) 2>/dev/null | tr -d ' \\n'"
}
read_ends() {
  local name=$1 dev=$2 blocks=$3 n=$4 hex
  hex=$(hexdump_of "dd if=/dev/block/$dev bs=4096 count=$n")
  [ "${#hex}" = $((n * 8192)) ] || { echo "$name $dev first $n blocks: short read (${#hex} hex characters)"; return 1; }
  printf '%s' "$hex" | xxd -r -p > "$dir/$name-first$n.bin"
  hex=$(hexdump_of "dd if=/dev/block/$dev bs=4096 skip=$((blocks - n)) count=$n")
  [ "${#hex}" = $((n * 8192)) ] || { echo "$name $dev last $n blocks: short read (${#hex} hex characters)"; return 1; }
  printf '%s' "$hex" | xxd -r -p > "$dir/$name-last$n.bin"
  echo "$name $dev ($blocks blocks of 4096 bytes): first and last $n blocks read"
}
rc=0
while read -r dev sectors lbs; do
  [ -n "${dev:-}" ] || continue
  # Linux always reports /sys/block/*/size in 512-byte units, whatever the logical block size is.
  blocks=$((sectors / 8))
  case $blocks in
    713728) read_ends lun4 "$dev" "$blocks" 8 || rc=1 ;;
    8192) read_ends lun5 "$dev" "$blocks" 8 || rc=1 ;;
    124271616) read_ends lun0 "$dev" "$blocks" 40 || rc=1 ;;
    *) echo "$dev: $blocks blocks of 4096 bytes (not read)" ;;
  esac
done <<< "$sizes"
exit $rc
