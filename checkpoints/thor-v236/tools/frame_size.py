#!/usr/bin/env python3
"""Stack bytes a function's prologue reserves (pre-indexed stp on sp plus sub sp, sp, #imm).
usage: frame_size.py <macho> <symbol> [...]"""
import re, subprocess, sys
objdump = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True).stdout.strip()
syms = {}
for l in subprocess.run(['xcrun', 'nm', '-n', '-arch', 'arm64', sys.argv[1]], capture_output=True, text=True).stdout.splitlines():
    p = l.split()
    if len(p) >= 3:
        try:
            syms.setdefault(' '.join(p[2:]), int(p[0], 16))
        except ValueError:
            pass
for name in sys.argv[2:]:
    if name not in syms:
        hit = [k for k in syms if name in k and '.__desc' not in k]
        if len(hit) != 1:
            print('%-40s no unique symbol (%d matches)' % (name, len(hit)))
            continue
        name = hit[0]
    a = syms[name]
    out = subprocess.run([objdump, '-d', '--arch=arm64', '--no-show-raw-insn', '--start-address=0x%x' % a,
                          '--stop-address=0x%x' % (a + 0x100), sys.argv[1]], capture_output=True, text=True).stdout
    total, seq = 0, []
    for l in out.splitlines():
        m = re.match(r'^\s*[0-9a-f]+:\s+(\S+)\s*(.*)$', l)
        if not m or m.group(1).endswith('>:'):
            continue
        mn, ops = m.group(1), m.group(2)
        ops = ops.split(';')[0].strip()
        seq.append((mn + ' ' + ops)[:44])
        if mn == 'stp' and '[sp, #-0x' in ops and ops.endswith(']!'):
            total += int(re.search(r'#-(0x[0-9a-f]+)\]!', ops).group(1), 16)
        elif mn == 'sub' and ops.startswith('sp, sp, #'):
            total += int(ops.split('#')[1].strip(), 16)
        if mn in ('bl', 'blr', 'b', 'br', 'ret'):
            break
    print('%-40s 0x%016x  frame 0x%-4x (%5d B) | %s' % (name[:40], a, total, total, ' ; '.join(seq[:5])))
