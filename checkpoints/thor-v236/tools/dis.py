#!/usr/bin/env python3
"""Disassemble one function (by exact symbol) from a kernel Mach-O using llvm-objdump.
usage: dis.py <macho> <symbol> [maxbytes]"""
import subprocess, sys
path, sym = sys.argv[1], sys.argv[2]
maxb = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
rows = []
for line in subprocess.run(['xcrun', 'nm', '-n', '-arch', 'arm64', path], capture_output=True, text=True).stdout.splitlines():
    p = line.split()
    if len(p) >= 3:
        try:
            rows.append((int(p[0], 16), p[1], ' '.join(p[2:])))
        except ValueError:
            pass
rows.sort()
hit = [r for r in rows if r[2] == sym or r[2].lstrip('_') == sym]
if not hit:
    print('no such symbol'); sys.exit(1)
addr = hit[0][0]
nxt = min([r[0] for r in rows if r[0] > addr] or [addr + maxb])
size = min(nxt - addr, maxb)
objdump = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True).stdout.strip()
out = subprocess.run([objdump, '-d', '--arch=arm64', '--no-show-raw-insn', '--start-address=0x%x' % addr, '--stop-address=0x%x' % (addr + size), path], capture_output=True, text=True).stdout
print(';; %s at 0x%x size 0x%x' % (sym, addr, size))
for l in out.splitlines():
    if l.strip().endswith('>:'):
        continue
    print(l)
