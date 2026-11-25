#!/usr/bin/env python3
"""Summarise an iOS .ips crash report (header JSON + body JSON).
usage: ips_summary.py <file.ips> [frames] [--all-threads]"""
import json, sys
args = [a for a in sys.argv[1:] if not a.startswith('--')]
path = args[0]; nframes = int(args[1]) if len(args) > 1 else 24
text = open(path, errors='replace').read()
hdr, body = text.split('\n', 1)
h = json.loads(hdr)
print('header: app=%s bug_type=%s timestamp=%s os=%s' % (h.get('app_name'), h.get('bug_type'), h.get('timestamp'), h.get('os_version')))
d = json.loads(body)
for k in ('procName', 'procPath', 'pid', 'parentProc', 'parentPid', 'procLaunch', 'exception', 'termination', 'faultingThread', 'responsibleProc', 'procExitAbsTime', 'codeSigningID'):
    if k in d:
        print('%-18s %s' % (k, json.dumps(d[k], ensure_ascii=False)[:420]))
if 'asi' in d:
    print('asi:', json.dumps(d['asi'], ensure_ascii=False)[:1500])
if 'legacyInfo' in d:
    print('legacyInfo:', json.dumps(d['legacyInfo'], ensure_ascii=False)[:300])
if 'lastExceptionBacktrace' in d:
    print('lastExceptionBacktrace present')
imgs = d.get('usedImages', [])
def show(th, n):
    for i, f in enumerate(th.get('frames', [])[:n]):
        ix = f.get('imageIndex', -1)
        img = imgs[ix] if 0 <= ix < len(imgs) else {}
        name = img.get('name') or '?'
        off = f.get('imageOffset')
        sym = f.get('symbol')
        symoff = f.get('symbolLocation')
        s = ('%s+%s' % (sym, symoff)) if sym else ''
        src = ('%s:%s' % (f.get('sourceFile'), f.get('sourceLine'))) if f.get('sourceFile') else ''
        print('  %2d %-34s +%-8s %s %s' % (i, name[:34], off, s, src))
ft = d.get('faultingThread', 0)
threads = d.get('threads', [])
if threads:
    th = threads[ft]
    print('faulting thread %d name=%r queue=%r frames=%d' % (ft, th.get('name'), th.get('queue'), len(th.get('frames', []))))
    show(th, nframes)
    if '--all-threads' in sys.argv:
        for i, t in enumerate(threads):
            if i != ft:
                print(' thread %d name=%r queue=%r' % (i, t.get('name'), t.get('queue')))
                show(t, 10)
