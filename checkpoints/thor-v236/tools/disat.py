#!/usr/bin/env python3
"""Disassemble a small window around kernel addresses (symbolised with llvm-objdump).
usage: disat.py <macho> <center-addr> [halfwidth]"""
import re, subprocess, sys
path, center = sys.argv[1], int(sys.argv[2], 16)
half = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x40
objdump = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True).stdout.strip()
lo, hi = center - half, center + half
out = subprocess.run([objdump, '-d', '--arch=arm64', '--no-show-raw-insn', '--start-address=0x%x' % lo, '--stop-address=0x%x' % hi, path], capture_output=True, text=True).stdout
for l in out.splitlines():
    if l.strip().endswith('>:') or not l.strip():
        continue
    m = re.match(r'^\s*([0-9a-f]+):', l)
    if not m:
        continue
    a = int(m.group(1), 16)
    mark = '  <== ' if a == center else '       '
    print(mark + l[24:])
