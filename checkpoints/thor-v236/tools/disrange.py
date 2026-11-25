#!/usr/bin/env python3
"""Disassemble [start, end) of a kernel Mach-O with llvm-objdump (symbols resolved by the tool).
usage: disrange.py <macho> <start-hex> <end-hex>"""
import subprocess, sys
path, lo, hi = sys.argv[1], int(sys.argv[2], 16), int(sys.argv[3], 16)
objdump = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True).stdout.strip()
out = subprocess.run([objdump, '-d', '--arch=arm64', '--no-show-raw-insn', '--start-address=0x%x' % lo, '--stop-address=0x%x' % hi, path], capture_output=True, text=True).stdout
mark = set(int(a, 16) for a in sys.argv[4:])
for l in out.splitlines():
    if l.strip().endswith('>:') or not l.strip():
        continue
    try:
        a = int(l.strip().split(':')[0], 16)
    except ValueError:
        continue
    print(('=> ' if a in mark else '   ') + l.strip())
