#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
inc=${THOR_ROOT}/thor-out/v237-unified/device/drivers/PongPower
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cp "$here"/{mock.h,test.cc,QcomUimStatusStub.h} "$tmp"/
cp "$inc"/{ThorMssServe.inc,QcomGlink.h,QcomScm.h,QcomSmem.h,ThorPdLocator.h,ThorQrtrProbe.h,ThorSsctlFields.h,ThorUimStatus.h} "$tmp"/
clang++ -std=c++17 -stdlib=libc++ -g -O1 -fsanitize=address,undefined -Wno-everything -I"$tmp" "$tmp"/test.cc -o "$tmp"/t 2>"$tmp"/cc.log
rc=$?
if [ $rc -ne 0 ]; then grep -E "error|warning: unused variable" "$tmp"/cc.log | head -30; exit 2; fi
"$tmp"/t
