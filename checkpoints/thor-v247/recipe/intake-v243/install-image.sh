#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Install a Thor image on the phone from this Mac, in one TWRP session (the phone starts in FASTBOOT):
#   install-image.sh <compact image> <sha256 of the image> [--dry-run] [--logs-done]
#   1. RAM-boots TWRP (nothing of the phone's own storage is written by that) and checks that it is
#      this phone, with the firmware LUN and boot_a exactly where they were read on 2026-11-27;
#   2. takes the logs of the session that just ended, read-only (--logs-done: they were just taken);
#   3. unless it is already done, marks boot slot a as booted successfully: the only change is boot_a's
#      attribute in the partition table of the firmware LUN, written as four blocks with the backup
#      first and the primary last, each block checked by its SHA-256 before it is written, and the
#      whole table read back and compared afterwards (agreed by the user on 2026-11-27; done once);
#   4. writes the image to boot_a with dd through TWRP, which leaves the partition tables and the slot
#      state alone (unlike "fastboot flash boot_a", which makes the slot unverified again), and
#      compares the SHA-256 of the whole partition with the image;
#   5. returns to the bootloader, reads the slot state there (read-only queries) and boots normally.
#
# Scope and safety: the only devices this script writes to are reached through "adb -s ${THOR_SERIAL} shell"
# with the phone running the RAM-booted TWRP: /dev/block/sde (the firmware LUN, four blocks, once) and
# /dev/block/sde14 (boot_a).  Nothing on this Mac is written except files under thor-out.  Every
# device command carries literal paths and numbers; the LUN is recognised by its size and by the disk
# GUID of its own table, read from the device right before anything is written.
#
#   --dry-run    steps 1 and 2, then read the table, plan the change, check every precondition on the
#                phone and print the commands; write nothing; back to fastboot with the keep-alive started
#   --logs-done  skip step 2 (the run that follows a dry run directly)
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v243-intake
tools=$out/session-tools
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
ad=/opt/homebrew/bin/adb
py=/opt/homebrew/bin/python3.13
probe=$tools/fbprobe
ram=$out/recovery-ram-trial/thor-twrp-ram-only-candidate.img
ram_sha=6dc604e4ce75fdae3ab89c6b9c453d51d7253dbdc237c0c41adb4cd06179ccd0
image=${1:?compact image}
image_sha=${2:?sha256 of the image}
shift 2
log=$intake/install.log
dry=${1:-}
logs_done=${2:-}
lun=/dev/block/sde                     # the firmware LUN, 713728 blocks of 4096 bytes
boot=/dev/block/sde14                  # boot_a on it, 49152 blocks of 4096 bytes
lun_blocks=713728
boot_blocks=49152
remote=/tmp/v243-install               # TWRP's /tmp is memory

note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
die() { note "END $*"; exit 1; }
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }
adb() { "$ad" -s "$serial" "$@"; }
S() { adb shell -n "$@" 2>/dev/null | tr -d '\r'; }
# One command on the phone; true only when the phone reports that it ended with 0.  (The status is read
# from the phone itself, so it does not depend on whether adb passes it on.)
R() { [ "$(S "$1; echo \" rc=\$?\"" | tail -1)" = " rc=0" ]; }
answers() { "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; }
getvar() { limited 10 "$fb" -s "$serial" getvar "$1" 2>&1 | tr -d '\r' | grep -E "^$1:|FAIL|error" | head -1 | tr -s " "; }
hex_of() { S "( $1 status=none | { command -v od >/dev/null 2>&1 && od -An -v -tx1 || hexdump -ve '1/1 \"%02x\"'; } ) 2>/dev/null | tr -d ' \\n'"; }
sha_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

[ "$logs_done" = "" ] || [ "$logs_done" = "--logs-done" ] || die "unknown option $logs_done"
[ "$dry" != "--dry-run" ] || [ "$logs_done" = "" ] || die "--logs-done is only for the real run"

note "START install $(basename "$image")${dry:+ ($dry)}"
[ "$dry" = "" ] || [ "$dry" = "--dry-run" ] || die "unknown option $dry"
for f in "$image" "$ram" "$tools/gpt-mark-ok.py" "$tools/gptcmp.py" "$out/v228-intake/full-snapshot.py"; do [ -f "$f" ] || die "missing $f"; done
[ "$(sha_of "$image")" = "$image_sha" ] || die "the image is not the verified one ($(basename "$image"))"
[ "$(sha_of "$ram")" = "$ram_sha" ] || die "the TWRP image is not the verified one"
image_bytes=$(stat -f %z "$image")
(( image_bytes % 4096 == 0 && image_bytes / 4096 < boot_blocks )) || die "image size $image_bytes does not fit boot_a in whole blocks"
image_blocks=$(( image_bytes / 4096 ))
fill_blocks=$(( boot_blocks - image_blocks ))
padded_sha=$("$py" -I -c "
import hashlib, sys
h = hashlib.sha256()
h.update(open(sys.argv[1], 'rb').read())
h.update(bytes(int(sys.argv[2]) * 4096))
print(h.hexdigest())" "$image" "$fill_blocks")
note "IMAGE $(basename "$image") sha256 $image_sha, $image_blocks blocks; $fill_blocks zero blocks follow; whole-partition sha256 $padded_sha"

# ---- 0. the keep-alive must not talk to the phone while this script does --------------------------
if [ -f "$intake/keepalive.pid" ]; then kill "$(cat "$intake/keepalive.pid")" 2>/dev/null && note "keep-alive stopped" || true; rm -f "$intake/keepalive.pid"; fi

# ---- 1. a steady link, then TWRP in RAM (skipped when the phone already runs the RAM-booted TWRP) ------------
state=$(adb get-state 2>/dev/null || true)
if [ "$state" != recovery ]; then
  for ((try = 1; try <= 48; try++)); do
    answers && break
    if "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d"; then
      note "SILENT fastboot (attempt $try): completing the interrupted download"
      "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"
    fi
    sleep 10
  done
  answers || die "fastboot-still-silent"
  stable=0
  for ((i = 0; i < 90 && stable < 30; i++)); do
    if "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d" && "$probe" cmd "getvar:product" 3000 2>&1 | grep -q "OKAYthor"; then
      stable=$((stable + 2))
    else
      stable=0
      note "link not steady yet (reset after $i probes)"
    fi
    sleep 2
  done
  [ "$stable" -ge 30 ] || die "link-never-steady"
  note "LINK steady for 30 s"
  booted=""
  for ((try = 1; try <= 3; try++)); do
    [ -z "$booted" ] || break
    sleep 4
    if limited 45 "$fb" -s "$serial" boot "$ram" >> "$log" 2>&1; then booted=1; else
      note "TWRP upload failed (attempt $try)"
      sleep 15
      answers || { "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"; sleep 15; }
    fi
  done
  [ -n "$booted" ] || die "twrp-boot-failed"
  state=""
  for ((i = 0; i < 90; i++)); do state=$(adb get-state 2>/dev/null || true); [ "$state" = recovery ] && break; sleep 1; done
else
  note "TWRP is already running: the fastboot steps are skipped"
fi
[ "$state" = recovery ] || die "recovery-not-enumerated (state=$state)"

# ---- 2. this phone, this LUN, this partition: all read from the phone before anything else -------------
[ "$(S 'getprop ro.product.device')" = thor ] || die "not thor"
[ "$(S 'id -u')" = 0 ] || die "not root in TWRP"
[ "$(S "blockdev --getsize64 $lun")" = $(( lun_blocks * 4096 )) ] || die "the firmware LUN does not have the expected size"
[ "$(S "blockdev --getss $lun")" = 4096 ] || die "the firmware LUN does not use 4096-byte blocks"
[ "$(S 'readlink -f /dev/block/by-name/boot_a')" = "$boot" ] || die "boot_a is not $boot"
[ "$(S "blockdev --getsize64 $boot")" = $(( boot_blocks * 4096 )) ] || die "boot_a does not have the expected size"
for tool in dd sha256sum readlink blockdev; do S "command -v $tool" | grep -q / || die "TWRP has no $tool"; done
S 'command -v od || command -v hexdump' | grep -q / || die "TWRP has neither od nor hexdump"
R true || die "the phone does not report the end of a command"
if R false; then die "the phone reports a failed command as successful"; fi
note "CHECKS device thor, root, /dev/block/sde is the firmware LUN, boot_a is $boot"

# ---- 3. the logs of the session that just ended, read-only (best effort) ---------------------------
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest=$out/recovery-$(basename "$image" .img | tr "+" " ")-before-$stamp
if [ "$logs_done" = "--logs-done" ]; then
  note "LOGS skipped: taken by the dry run just before"
elif "$py" -I "$out/v228-intake/full-snapshot.py" "$out/../UniXNU-pong/tools/apfs" dump "$dest" logs mobile/Library/Logs mobile/Library/Preferences > "$intake/before-dump.log" 2>&1; then
  note "LOGS rc=0 $(tail -1 "$intake/before-dump.log" | cut -c1-120) -> $(basename "$dest")"
else
  note "LOGS rc=$? the dump did not complete ($(tail -1 "$intake/before-dump.log" | cut -c1-120)); the earlier sessions of this image are already saved"
fi
[ "$(adb get-state 2>/dev/null)" = recovery ] || die "TWRP lost after the dump"

# ---- 4. the partition table as it is now, and the plan ------------------------------------------------
work=$intake/mark-$stamp
mkdir -p "$work"
hex=$(hex_of "dd if=$lun bs=4096 count=8" || true); [ "${#hex}" = 65536 ] || die "short read of the first blocks (${#hex})"
printf '%s' "$hex" | xxd -r -p > "$work/first8.bin"
hex=$(hex_of "dd if=$lun bs=4096 skip=$(( lun_blocks - 8 )) count=8" || true); [ "${#hex}" = 65536 ] || die "short read of the last blocks (${#hex})"
printf '%s' "$hex" | xxd -r -p > "$work/last8.bin"
"$py" -I "$tools/gptcmp.py" "$work/first8.bin" "$work/last8.bin" 8 > "$work/before.txt" 2>&1 || true
grep -q 'entry arrays identical: True' "$work/before.txt" || { note "$(< "$work/before.txt" | head -8 | tr '\n' ';')"; die "the two tables of the firmware LUN are not in order; not touching them"; }
grep -E '  13 boot_a|  42 boot_b' "$work/before.txt" | sed 's/^ */  before: /' | tee -a "$log" || true
set +e
"$py" -I "$tools/gpt-mark-ok.py" "$work/first8.bin" "$work/last8.bin" "$work/plan" > "$work/plan.log" 2>&1
plan_rc=$?
set -e
cat "$work/plan.log" | tee -a "$log"
if [ "$plan_rc" = 3 ]; then
  note "SLOT the table already marks boot_a successful (written once on 2026-11-27): the partition table is not touched"
  mark=0
elif [ "$plan_rc" = 0 ]; then
  mark=1
else
  die "no plan (rc=$plan_rc): nothing is written"
fi
plan=$work/plan
if [ "$mark" = 1 ]; then
  # the plan's result, checked once more by the reader that does not know how the plan was made
  "$py" -I "$tools/gptcmp.py" "$plan/after-first8.bin" "$plan/after-last8.bin" 8 > "$work/plan-check.txt" 2>&1 || true
  grep -q 'entry arrays identical: True' "$work/plan-check.txt" && grep -q 'table CRC primary .* EQUAL' "$work/plan-check.txt" || die "the planned table does not verify"
  grep -q '  13 boot_a .* attrs=0x007f000000000000' "$work/plan-check.txt" || die "the plan does not mark boot_a as expected"
  a=$(shasum -a 256 "$work/first8.bin" | cut -c1-64); b=$(shasum -a 256 "$plan/first8.bin" | cut -c1-64)
  [ "$a" = "$b" ] || die "the plan was not made from the blocks just read"
  # every block that will be written must hold, right now, the bytes the plan expects it to hold
  check_block() { # file-with-block lba
    local want; want=$(sha_of "$1")
    local hex2; hex2=$(hex_of "dd if=$lun bs=4096 skip=$2 count=1" || true)
    [ "${#hex2}" = 8192 ] || die "short read of block $2"
    printf '%s' "$hex2" | xxd -r -p > "$work/now-$2.bin"
    [ "$(sha_of "$work/now-$2.bin")" = "$want" ] || die "block $2 does not hold what was read a moment ago"
  }
  for item in b-entries:713723 b-header:713727 p-entries:2 p-header:1; do
    f=${item%%:*}; l=${item##*:}
    before_file=$work/first8.bin; off=$(( (l) * 4096 ))
    if [ "$l" -gt 8 ]; then before_file=$work/last8.bin; off=$(( (l - (lun_blocks - 8)) * 4096 )); fi
    "$py" -I -c "import sys; d = open(sys.argv[1], 'rb').read(); o = int(sys.argv[3]); open(sys.argv[2], 'wb').write(d[o:o + 4096])" "$before_file" "$work/was-$f.bin" "$off"
    check_block "$work/was-$f.bin" "$l" && note "  block $l holds the bytes read before ($f)"
  done
  write_cmd() { # file lba
    echo "dd if=$remote/$1.bin of=$lun bs=4096 seek=$2 count=1 conv=fsync status=none"
  }
  {
    echo "planned writes, in this order (the backup first, the primary last):"
    for item in b-entries:713723 b-header:713727 p-entries:2 p-header:1; do echo "  $(write_cmd ${item%%:*} ${item##*:})"; done
  } | tee -a "$log"
fi
{
  echo "then: dd if=$remote/boot.img of=$boot bs=1048576 conv=fsync status=none"
  echo "      dd if=/dev/zero of=$boot bs=4096 seek=$image_blocks count=$fill_blocks conv=fsync status=none"
  echo "      sha256 of $boot expected $padded_sha"
} | tee -a "$log"

if [ "$dry" = "--dry-run" ]; then
  rm -f "$work"/now-*.bin "$work"/was-*.bin
  adb reboot bootloader
  note "END dry-run: nothing written; back to fastboot, keep-alive started"
  nohup /bin/bash "$intake/keepalive.sh" >/dev/null 2>&1 &
  echo $! > "$intake/keepalive.pid"
  exit 0
fi

# ---- 5. mark slot a successful: four blocks ---------------------------------------------------------
if [ "$mark" = 1 ]; then
  S "mkdir -p $remote" >/dev/null
  for item in b-entries:713723 b-header:713727 p-entries:2 p-header:1; do
    f=${item%%:*}
    adb push "$plan/$f.bin" "$remote/$f.bin" >/dev/null 2>&1 || die "push of $f failed"
    [ "$(S "sha256sum $remote/$f.bin" | cut -d' ' -f1)" = "$(sha_of "$plan/$f.bin")" ] || die "$f on the phone is not the planned block"
  done
  note "PUSHED the four planned blocks; each one's SHA-256 on the phone is the planned one"
  # the targets have not changed since the check above
  for item in b-entries:713723 b-header:713727 p-entries:2 p-header:1; do check_block "$work/was-${item%%:*}.bin" "${item##*:}"; done
  for item in b-entries:713723 b-header:713727 p-entries:2 p-header:1; do
    f=${item%%:*}; l=${item##*:}
    R "dd if=$remote/$f.bin of=$lun bs=4096 seek=$l count=1 conv=fsync status=none" || die "writing block $l failed; stopping here"
    note "WROTE block $l ($f)"
  done
  R "sync" || true
  hex=$(hex_of "dd if=$lun bs=4096 count=8" || true); printf '%s' "$hex" | xxd -r -p > "$work/after-first8.bin"
  hex=$(hex_of "dd if=$lun bs=4096 skip=$(( lun_blocks - 8 )) count=8" || true); printf '%s' "$hex" | xxd -r -p > "$work/after-last8.bin"
  [ "${#hex}" = 65536 ] || die "short read after the write"
  [ "$(sha_of "$work/after-first8.bin")" = "$(sha_of "$plan/after-first8.bin")" ] || die "the first blocks differ from what was planned after the write"
  [ "$(sha_of "$work/after-last8.bin")" = "$(sha_of "$plan/after-last8.bin")" ] || die "the last blocks differ from what was planned after the write"
  "$py" -I "$tools/gptcmp.py" "$work/after-first8.bin" "$work/after-last8.bin" 8 > "$work/after.txt" 2>&1 || true
  grep -q 'entry arrays identical: True' "$work/after.txt" && grep -q 'table CRC primary .* EQUAL' "$work/after.txt" || die "the table does not verify after the write"
  grep -E '  13 boot_a' "$work/after.txt" | sed 's/^ */  after:  /' | tee -a "$log" || true
  R "rm -f $remote/b-entries.bin $remote/b-header.bin $remote/p-entries.bin $remote/p-header.bin"
  note "MARKED slot a: the table read back is exactly the planned one"
fi

# ---- 6. the image into boot_a --------------------------------------------------------------------------
free_kb=$(S 'df -k /tmp | tail -1' | awk '{print $4}' || true)
[ -n "$free_kb" ] && [ "$free_kb" -gt $(( image_bytes / 1024 + 65536 )) ] || die "not enough memory for /tmp in TWRP ($free_kb KB free)"
had=$(S "sha256sum $boot" | cut -d' ' -f1 || true)
note "BOOT before: sha256 of the whole partition $had"
adb push "$image" "$remote/boot.img" >/dev/null 2>&1 || die "push of the image failed"
[ "$(S "sha256sum $remote/boot.img" | cut -d' ' -f1)" = "$image_sha" ] || die "the image on the phone is not the verified one"
[ "$(S 'readlink -f /dev/block/by-name/boot_a')" = "$boot" ] || die "boot_a is not $boot any more"
R "dd if=$remote/boot.img of=$boot bs=1048576 conv=fsync status=none" || die "writing the image failed"
R "dd if=/dev/zero of=$boot bs=4096 seek=$image_blocks count=$fill_blocks conv=fsync status=none" || die "filling the rest of the partition failed"
R "sync" || true
got=$(S "sha256sum $boot" | cut -d' ' -f1 || true)
note "BOOT after: sha256 of the whole partition $got"
[ "$got" = "$padded_sha" ] || die "the partition does not hold the image"
R "rm -f $remote/boot.img"
note "FLASHED v243 into boot_a: the whole partition reads back as the verified image"

# ---- 7. back to the bootloader: the slot state, then a normal boot ---------------------------------
adb reboot bootloader
present=""
for ((i = 0; i < 60; i++)); do "$fb" devices 2>/dev/null | grep -q "^$serial" && present=1 && break; sleep 2; done
[ -n "$present" ] || die "not-back-in-fastboot"
stable=0
for ((i = 0; i < 90 && stable < 12; i++)); do
  if answers; then stable=$((stable + 2)); else stable=0; fi
  sleep 2
done
[ "$stable" -ge 12 ] || die "fastboot-not-steady"
slot=$(getvar current-slot || true); ok=$(getvar slot-successful:a || true); tries=$(getvar slot-retry-count:a || true); unboot=$(getvar slot-unbootable:a || true)
note "SLOT $slot; $ok; $tries; $unboot"
# The bootloader's own words decide whether a normal boot is started: only a slot that it calls
# successful, not unbootable, with all seven retries, is booted; anything else is left to a person.
if [ "$slot" != "current-slot: a" ] || [ "$ok" != "slot-successful:a: yes" ] || [ "$tries" != "slot-retry-count:a: 7" ] || [ "$unboot" != "slot-unbootable:a: no" ]; then
  note "END slot-state-unexpected: the phone stays in fastboot (keep-alive started); nothing is booted"
  nohup /bin/bash "$intake/keepalive.sh" >/dev/null 2>&1 &
  echo $! > "$intake/keepalive.pid"
  exit 2
fi
if limited 20 "$fb" -s "$serial" reboot >> "$log" 2>&1; then note "REBOOT requested: the phone boots $(basename "$image" .img) from flash"; else note "REBOOT command not confirmed (rc=$?): enter fastboot again and run: fastboot reboot"; fi
note "END done"
