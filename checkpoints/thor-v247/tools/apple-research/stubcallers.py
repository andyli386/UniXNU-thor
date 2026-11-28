"""stubcallers.py <binary> <symbol-substring>...: functions that call the named stubs"""
import re, struct, subprocess, sys
sys.path.insert(0, '/tmp/claude-501/cont')
from macho import Macho
m = Macho(sys.argv[1])
pats = sys.argv[2:]
targets = {a: n for a, n in m._stub.items() if n and any(p in n for p in pats)}
text = subprocess.run(['xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', sys.argv[1]], capture_output=True, text=True).stdout
hits = {}
for line in text.splitlines():
    mm = re.match(r'^\s*([0-9a-f]+):\s+bl\s+(0x[0-9a-f]+)', line)
    if not mm: continue
    t = int(mm.group(2), 16)
    if t in targets:
        f = m.func(int(mm.group(1), 16))
        hits.setdefault(targets[t], set()).add(f[0] if f else None)
for n in sorted(hits):
    print(n)
    for f in sorted(x for x in hits[n] if x is not None):
        print('    %#x' % f)
