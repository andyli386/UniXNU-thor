#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source prepare/env.sh
set +e
( set -e
  bash build-kernel.sh final
  cp -p build-tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/mach.development.pong.unstripped kernel/
) >prepare/build.log 2>&1
status=$?
echo "$status" >prepare/build.status
exit "$status"
