import re, sys
lines = open(sys.argv[1]).read().splitlines()
target = re.compile(sys.argv[2])
imm = re.compile(r'mov\s+w[1-6], #(0x[0-9a-f]+)')
fn = ''
hdr = re.compile(r'^[A-Za-z_][A-Za-z0-9_$.]*:$')
for i, l in enumerate(lines):
    s = l.strip()
    if hdr.match(s) and not s.startswith('loc_'): fn = s.rstrip(':')
    if target.search(l) and re.search(r'\s(bl|b)\s', l):
        ctx = []
        for j in range(max(0, i-10), i):
            m = imm.search(lines[j])
            if m: ctx.append('%s=%s' % (m.group(0).split(',')[0].split()[-1], m.group(1)))
            if re.search(r'\sbl\s', lines[j]): ctx.append('|')
        print('%-62s %s   %s' % (fn, l.split('\t')[-1].strip()[:40], ' '.join(ctx[-9:])))
