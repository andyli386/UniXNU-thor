#!/usr/bin/env python3.13
"""Functions of an on-disk Apple binary that reference a C string or an address.
   xref.py <binary> <substring of C string> | <0xaddress>"""
import re, struct, subprocess, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from macho import Macho
m = Macho(sys.argv[1])
arg = sys.argv[2]
if arg.startswith('0x'):
    targets = {int(arg, 16): ''}
else:
    targets = {}
    for n, sname, addr, ssize, soff, flags, r1, r2 in m.sects:
        if sname in ('__cstring', '__oslogstring', '__objc_methname', '__const'):
            blob = m.data[soff:soff + ssize]
            for mm in re.finditer(re.escape(arg.encode()), blob):
                s = blob.rfind(b'\0', 0, mm.start()) + 1
                e = blob.find(b'\0', mm.start())
                targets[addr + s] = blob[s:e].decode('latin1')
# constant CFStrings that hold one of the strings: code refers to the constant, not to the characters
cf = {}
for n, sname, addr, ssize, soff, flags, r1, r2 in m.sects:
    if sname == '__cfstring':
        for i in range(ssize // 32):
            v = struct.unpack_from('<Q', m.data, soff + i * 32 + 16)[0]
            if v >> 63:
                continue
            if v >> 32:
                v &= 0xFFFFFFFFF
            if v and v < m.base:
                v += m.base
            if v in targets:
                cf[addr + i * 32] = targets[v]
targets.update(cf)
text = subprocess.run(['xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', sys.argv[1]], capture_output=True, text=True).stdout
pages, hits, funcs = {}, {}, {}
LINE = re.compile(r'\s*([0-9a-f]+):\s+(\S+)\s*(.*?)\s*(?:<[^>]*>)?\s*$')
for line in text.splitlines():
    mm = LINE.match(line)
    if not mm:
        continue
    addr, op, ops = int(mm.group(1), 16), mm.group(2), mm.group(3)
    g = re.match(r'^(x\d+), (0x[0-9a-f]+)$', ops)
    if op == 'adrp' and g:
        pages[g.group(1)] = int(g.group(2), 16)
        continue
    g = re.match(r'^(x\d+), (x\d+), #(0x[0-9a-f]+|\d+)$', ops)
    if op == 'add' and g and g.group(2) in pages:
        t = pages[g.group(2)] + int(g.group(3), 0)
        if t in targets:
            hits.setdefault(t, set()).add(m.func(addr)[0])
for t, fs in sorted(hits.items()):
    print('%#x %r' % (t, targets[t][:150]))
    for f in sorted(fs):
        print('    function %#x' % f)
