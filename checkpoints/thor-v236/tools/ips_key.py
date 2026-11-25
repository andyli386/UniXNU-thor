#!/usr/bin/env python3
"""Key fields of .ips reports (tolerant of truncated bodies).
usage: ips_key.py <file-or-dir.ips> [...]   — for a directory, every non-SiriSearchFeedback file is summarised."""
import json, os, re, sys
def brace(s, i):
    depth = 0; instr = False; esc = False
    for j in range(i, len(s)):
        c = s[j]
        if instr:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == '"': instr = False
        else:
            if c == '"': instr = True
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: return s[i:j + 1]
    return None
def field(body, name):
    m = re.search(r'"%s"\s*:\s*' % re.escape(name), body)
    if not m: return None
    i = m.end()
    if body[i] == '{' or body[i] == '[':
        t = brace(body, i)
        try:
            return json.loads(t) if t else 'TRUNCATED ' + body[i:i + 200].replace('\n', ' ')
        except Exception:
            return 'UNPARSED ' + (t or body[i:i + 300]).replace('\n', ' ')[:300]
    m2 = re.match(r'"((?:[^"\\]|\\.)*)"', body[i:])
    if m2: return m2.group(1)
    m3 = re.match(r'[^,\n]*', body[i:]); return m3.group(0).strip() if m3 else None
def one(path):
    s = open(path, errors='replace').read()
    hdr, body = s.split('\n', 1)
    try:
        h = json.loads(hdr)
    except Exception:
        h = {}
    print('== %s (%d bytes, complete json: %s)' % (os.path.basename(path), len(s), body.rstrip().endswith('}')))
    for k in ('bug_type', 'procName', 'pid', 'parentProc', 'captureTime', 'uptime', 'procLaunch', 'exception', 'faultingThread'):
        v = field(body, k)
        if v is not None:
            print('   %-13s %s' % (k, json.dumps(v, ensure_ascii=False)[:300] if not isinstance(v, str) else v[:300]))
    t = field(body, 'termination')
    if t is not None:
        if isinstance(t, dict):
            print('   termination   namespace=%s code=%s flags=%s' % (t.get('namespace'), t.get('code'), t.get('flags')))
            for d in t.get('details', [])[:14]:
                print('       ' + str(d)[:260])
        else:
            print('   termination   ' + str(t)[:400])
    a = field(body, 'asi')
    if a: print('   asi ' + json.dumps(a, ensure_ascii=False)[:600])
    m = field(body, 'memoryStatus')
    if isinstance(m, dict):
        mp = m.get('memoryPages', {})
        print('   memory        pressure=%s free=%s active=%s inactive=%s wired=%s spec=%s compressorSize=%s pagesWanted=%s' % (
            m.get('memoryPressure'), mp.get('free'), mp.get('active'), mp.get('inactive'), mp.get('wired'), mp.get('speculative'), m.get('compressorSize'), (m.get('memoryPressureDetails') or {}).get('pagesWanted')))
for p in sys.argv[1:]:
    if os.path.isdir(p):
        for n in sorted(os.listdir(p)):
            if n.endswith('.ips') and 'SiriSearchFeedback' not in n:
                one(os.path.join(p, n))
    else:
        one(p)
