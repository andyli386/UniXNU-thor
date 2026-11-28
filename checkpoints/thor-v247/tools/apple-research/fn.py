#!/usr/bin/env python3.13
"""fn.py <0xaddr|exact label> [grep -E pattern] [maxlines]: annotated lines of the whole function containing addr (label lines start with a non-hex char).
   env DIS (default /tmp/claude-501/cont/research-b/wifid.dis)"""
import os, re, sys
dis = os.environ.get('DIS', '/tmp/claude-501/cont/research-b/wifid.dis')
arg = sys.argv[1]
pat = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != '-' else None
maxl = int(sys.argv[3]) if len(sys.argv) > 3 else 400
lines = open(dis, encoding='utf-8', errors='replace').read().split('\n')
islabel = lambda l: bool(l) and not re.match(r'^[0-9a-f]+:', l) and l.endswith(':')
idx = None
if arg.startswith('0x'):
    a = arg[2:]
    for i, l in enumerate(lines):
        if l.startswith(' ' * 0) and re.match(r'^\s*%s:' % a, l):
            idx = i; break
else:
    for i, l in enumerate(lines):
        if l == arg + ':':
            idx = i; break
if idx is None:
    sys.exit('not found ' + arg)
s = idx
while s > 0 and not islabel(lines[s]):
    s -= 1
e = s + 1
while e < len(lines) and not islabel(lines[e]):
    e += 1
out = lines[s:e]
if pat:
    rx = re.compile(pat)
    out = [l for l in out if rx.search(l)]
print('\n'.join(out[:maxl]))
if len(out) > maxl:
    print('... [%d more lines]' % (len(out) - maxl), file=sys.stderr)
