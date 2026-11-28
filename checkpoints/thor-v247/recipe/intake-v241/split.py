#!/usr/bin/env python3.13
"""Split the last boot's kernel console out of the mirror file (not truncated at boot) by comparing
it with the previous run's copy; the new run's part ends at the last differing byte.
   split.py <new mirror> <previous mirror> <output>"""
import sys
b = open(sys.argv[1], 'rb').read(); c = open(sys.argv[2], 'rb').read()
i = min(len(b), len(c)) - 1
while i >= 0 and b[i] == c[i]:
    i -= 1
nl = b.rfind(b'\n', 0, i + 1)
part = b[:nl + 1]
open(sys.argv[3], 'wb').write(part)
lines = part.decode('utf-8', 'replace').split('\n')
print('new console: %d bytes, %d lines; first %s; last %s' % (len(part), len(lines), lines[0][:44], lines[-2][:60] if len(lines) > 1 else ''))
# a second boot inside the same part (a short run over an older longer one leaves the older tail)
starts = [k for k, l in enumerate(lines) if 'Darwin Kernel Version' in l]
print('kernel starts at lines', starts)
