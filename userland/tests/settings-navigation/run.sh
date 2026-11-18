#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export ASAN_OPTIONS=detect_leaks=0
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
flags=(-O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined)
xcrun --sdk macosx clang "${flags[@]}" -framework CoreFoundation -lobjc tests/settings-navigation/test-spans.c -o "$out/spans"
"$out/spans"
xcrun --sdk macosx clang "${flags[@]}" -fblocks -fobjc-arc -I tests/settings-navigation -framework Foundation \
    navigation_completion185.m tests/settings-navigation/test-coordinator.m -o "$out/coordinator"
"$out/coordinator"
