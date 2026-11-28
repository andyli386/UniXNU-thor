#!/usr/bin/env python3
"""IOKit call sites of an extracted dylib: resolve __stubs entries to import names via the indirect symbol table.
usage: iocalls.py <dylib> [name-substr...] [--imm]
Prints per function (exported symbols) each bl to an import whose name matches IOKit/CF/dispatch, with the immediates
in the preceding instructions."""
import re, struct, subprocess, sys
sys.path.insert(0, '/tmp/claude-501/cont')
from macho import Macho
m = Macho(sys.argv[1])
data = m.data
# symbol table
symoff, nsyms, stroff, strsize = m.symtab
# indirect symbols
dysym = m.dysym
indiroff, nindirect = dysym[12], dysym[13]
stubs = {}
for n, sname, addr, ssize, soff, flags, r1, r2 in m.sects:
    if sname == '__stubs' and (flags & 0xff) == 0x8:   # S_SYMBOL_STUBS
        stride = r2 if r2 else (flags >> 8) or 12
        for i in range(ssize // stride):
            isym = struct.unpack_from('<I', data, indiroff + 4 * (r1 + i))[0]
            if isym == 0x80000000: continue
            n_strx = struct.unpack_from('<I', data, symoff + 16 * isym)[0]
            e = data.find(b'\0', stroff + n_strx)
            stubs[addr + i * stride] = data[stroff + n_strx:e].decode()
names = {}
out = subprocess.run(['xcrun', 'nm', '-n', sys.argv[1]], capture_output=True, text=True).stdout
for line in out.splitlines():
    p = line.split()
    if len(p) == 3 and re.fullmatch(r'[0-9a-f]+', p[0]) and p[1] in ('T', 't'):
        names[int(p[0], 16)] = p[2]
args = [a for a in sys.argv[2:] if not a.startswith('--')]
want = args
show_imm = '--imm' in sys.argv
LINE = re.compile(r'\s*([0-9a-f]+):\s+(\S+)\s*(.*?)\s*(?:<[^>]*>)?\s*$')
for addr, name in sorted(names.items()):
    if want and not any(w in name for w in want): continue
    r = m.func(addr)
    if not r: continue
    a, b = r
    text = subprocess.run(['xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', '--start-address=%#x' % a, '--stop-address=%#x' % b, sys.argv[1]], capture_output=True, text=True).stdout
    ins = []
    for line in text.splitlines():
        mm = LINE.match(line)
        if mm: ins.append((int(mm.group(1), 16), mm.group(2), mm.group(3)))
    rows = []
    for i, (ad, op, ops) in enumerate(ins):
        if op in ('bl', 'b') and ops.startswith('0x'):
            tgt = int(ops.split()[0], 16)
            if tgt in stubs:
                ctx = []
                for ad2, op2, ops2 in ins[max(0, i-12):i]:
                    if re.search(r'#0x[0-9a-f]+\b', ops2) and op2 in ('mov', 'movz', 'orr', 'cmp', 'movk'):
                        ctx.append('%s %s' % (op2, ops2))
                    elif show_imm:
                        ctx.append('%s %s' % (op2, ops2))
                rows.append('  %#x %-40s <- %s' % (ad, stubs[tgt], ' | '.join(ctx[-7:])))
    if rows:
        print('==== %s  %#x..%#x' % (name, a, b))
        print('\n'.join(rows))
