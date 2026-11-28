#!/bin/bash
# THOR CAMERA V247 host test. Compiles the pure headers (PongCamBoard.h, PongCamCc.h, PongCamCci.h, PongCamPower.h,
# PongCamInventory.h) with scripted fakes of CAM_CC, the CCI, RPMh and the SPMI arbiter and asserts the clock and
# CCI sequences, the bounded waits, the roll-back after each injected failure, and that level 1 only reads.
# Needs a clang++ (Xcode's by default, or CC=...) and ThorModemCmdDb.h from a PongPower directory: the parent of
# this directory once the headers are integrated, else PONG_POWER_DIR=... (default: the v246 recipe's copy).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd -P)
cc=${CC:-/usr/bin/clang++}
power="$here/.."                       # the PongPower directory once the headers are integrated
[ -f "$power/ThorModemCmdDb.h" ] || power="${PONG_POWER_DIR:-$here/../../v246-unified/device/drivers/PongPower}"
[ -f "$power/ThorModemCmdDb.h" ] || { echo "ThorModemCmdDb.h not found under $power; set PONG_POWER_DIR" >&2; exit 2; }
tmp=$(mktemp -d "${TMPDIR:-/tmp}/pong-cam-hosttest.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
"$cc" -std=c++17 -Wall -Wextra -Werror -Wno-unused-function -fsanitize=address,undefined -g -O1 \
  -I"$here/.." -I"$power" "$here/test_cam.cpp" -o "$tmp/test_cam"
"$tmp/test_cam"
