#!/usr/bin/env python3.13
"""Call-graph index of the annotated disassembly:  xrim.py <dis file> <out>
   out.calls:  for every function (header): the callees (named) one per line, in address order
   out.xref:   for every named callee (stub / objc selector / local named function): the list of callers
"""
import re, sys, collections, bisect
dis, out = sys.argv[1], sys.argv[2]
LINE = re.compile(r'^([0-9a-f]+):  (.*)$')
cur = None
calls = collections.OrderedDict()
xref = collections.defaultdict(set)
for line in open(dis, encoding='utf-8', errors='replace'):
    line = line.rstrip('\n')
    m = LINE.match(line)
    if not m:
        if line and not line.startswith(' ') and line.endswith(':'):
            cur = line[:-1]
            calls.setdefault(cur, [])
        continue
    if cur is None:
        continue
    body = m.group(2)
    if body.startswith('bl\t') or body.startswith('\tb') or re.match(r'^b\.\w+\t', body):
        nm = body.split(';', 1)[1].strip() if ';' in body else body.split('\t')[1].strip()
        calls[cur].append((int(m.group(1), 16), nm))
        xref[nm].add(cur)
with open(out + '.calls', 'w') as f:
    for fn, lst in calls.items():
        f.write('%s:\n' % fn)
        for a, nm in lst:
            f.write('    %x  %s\n' % (a, nm))
with open(out + '.xref', 'w') as f:
    for nm in sorted(xref):
        f.write('%s:\n' % nm)
        for c in sorted(xref[nm]):
            f.write('    %s\n' % c)
print(len(calls), 'functions', len(xref), 'callees', file=sys.stderr)
