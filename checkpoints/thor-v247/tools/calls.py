#!/usr/bin/env python3.13
"""Annotated calls of a function in an on-disk Apple binary.

   calls.py <binary> <hex addr> [--raw]   the function containing addr: every call with its stub symbol, every string
                                          and selector reference, and the conditional branches that skip a call.
                                          --raw also prints the other instructions."""
import re, struct, subprocess, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from macho import Macho
m = Macho(sys.argv[1])
va = int(sys.argv[2], 16)
r = m.func(va)
if not r:
    sys.exit('no function at %#x' % va)
a, b = r
text = subprocess.run(['xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', '--start-address=%#x' % a, '--stop-address=%#x' % b, sys.argv[1]], capture_output=True, text=True).stdout
raw = '--raw' in sys.argv
print('function %#x..%#x (%d bytes)' % (a, b, b - a), file=sys.stderr)

def pointer(va_):
    """the unchained pointer stored at va_, or None"""
    o = m.off(va_)
    if o is None or o + 8 > len(m.data):
        return None
    v = struct.unpack_from('<Q', m.data, o)[0]
    if v >> 63:                      # a chained-fixups bind: the target is not in the file
        return None
    if v >> 32:                      # a chained-fixups rebase: the target is the low 36 bits, relative to the image
        v &= 0xFFFFFFFFF
    if v and v < m.base:
        v += m.base
    return v

pages = {}
LINE = re.compile(r'\s*([0-9a-f]+):\s+(\S+)\s*(.*?)\s*(?:<[^>]*>)?\s*$')
for line in text.splitlines():
    mm = LINE.match(line)
    if not mm:
        continue
    addr, op, ops = int(mm.group(1), 16), mm.group(2), mm.group(3)
    out, note = None, ''
    g = re.match(r'^(x\d+), (0x[0-9a-f]+)$', ops)
    if op == 'adrp' and g:
        pages[g.group(1)] = int(g.group(2), 16)
    else:
        g = re.match(r'^(x\d+), (x\d+), #(0x[0-9a-f]+|\d+)$', ops)
        if op == 'add' and g and g.group(2) in pages:
            s = m.cstr(pages[g.group(2)] + int(g.group(3), 0))
            if s and len(s) > 1 and s.isprintable():
                note = '; %r' % s[:500]
            pages.pop(g.group(2), None)
        else:
            g = re.match(r'^([xdq]\d+), \[(x\d+)(?:, #(0x[0-9a-f]+|\d+))?\]$', ops)
            if op in ('ldr', 'ldur', 'ldrb', 'str') and g and g.group(2) in pages:
                target = pages[g.group(2)] + (int(g.group(3), 0) if g.group(3) else 0)
                p = pointer(target) if op != 'str' else None
                if p is not None:
                    s = m.cstr(p)
                    if s and s.isprintable():
                        note = '; -> %r' % s[:200]
                pages.pop(g.group(2), None)
            elif op in ('bl', 'blr'):
                name = m.stub(int(ops, 16)) if op == 'bl' and ops.startswith('0x') else None
                out = '%s %s' % (op, ops)
                if name:
                    note = '; ' + name
                elif op == 'bl' and ops.startswith('0x'):
                    t = int(ops, 16)
                    f = m.func(t)
                    if f is None:
                        note = '; unknown target'
                    elif f[0] == t:
                        note = '; local function %#x' % t
                    else:
                        note = '; into %#x + %#x' % (f[0], t - f[0])
            elif op in ('cbz', 'cbnz', 'tbz', 'tbnz'):
                t = ops.split(', ')[-1]
                if t.startswith('0x'):
                    note = '-> %#x' % int(t, 16)
            elif op in ('mov', 'movz', 'movk', 'movq') or op.startswith('fmov'):
                for reg in list(pages):
                    if ops.startswith(reg + ','):
                        pages.pop(reg)
            elif op in ('b', 'b.eq', 'b.ne', 'b.lo', 'b.hs', 'b.lt', 'b.ge', 'b.gt', 'b.le', 'b.mi', 'b.pl', 'b.al'):
                t = ops
                if raw:
                    out = '%s %s' % (op, ops)
    if out or note:
        print('%#x  %-40s %s' % (addr, out or (op + ' ' + ops), note))
    elif raw:
        print('%#x  %s %s' % (addr, op, ops))
