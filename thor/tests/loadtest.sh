#!/bin/bash
# The boot-image side of the OS patch on the Mac: thorOsPatchLoad is copied into the test by hand; this refuses to
# run when the copy and the driver differ, then builds the test and prints its command line.
#   loadtest.sh <kernel tree with bsd/kern/thor_os_patch.h> <output binary>
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
driver=$(cd "$here/../../drivers/NativeUfs" && pwd -P)/PongUfsTransport.cpp
test=$here/boot_image_loader_test.c
body() { # the text of thorOsPatchLoad, from its first line to its closing brace
  awk '/^static int thorOsPatchLoad/{on=1} on{print} on&&/^}/{exit}' "$1" | sed -e 's/gHost\.virt + kDataOffset/PAYLOAD/g' -e 's/gHost_virt_plus_kDataOffset/PAYLOAD/g' -e 's/!= kIOReturnSuccess/!= 0/g'
}
if ! diff <(body "$driver") <(body "$test") > /dev/null; then
  echo "boot_image_loader_test.c does not hold the thorOsPatchLoad of PongUfsTransport.cpp:" >&2
  diff <(body "$driver") <(body "$test") >&2 || true
  exit 3
fi
xcrun clang++ -x c++ -std=c++17 -O1 -Wall -Wextra -Werror -Wno-unused-parameter -Wno-gnu-zero-variadic-macro-arguments -o "$2" "$test" -I "$1/bsd/kern"
echo "built $2; run: $2 <copy of the window> <boot image> <thor-os-patch-offset> <thor-os-patch-bytes> <window bytes>"
