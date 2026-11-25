import json, sys
p = sys.argv[1]
rows = []
with open(p, encoding='utf-8', errors='replace') as f:
    for i, line in enumerate(f):
        try:
            d = json.loads(line)
        except Exception as e:
            rows.append((i, 'BAD', '', str(e)[:80]))
            continue
        t = d.get('type')
        ts = d.get('timestamp', '')
        m = d.get('message') or {}
        c = m.get('content')
        summ = ''
        if isinstance(c, str):
            summ = 'STR:' + c
        elif isinstance(c, list):
            parts = []
            for b in c:
                if not isinstance(b, dict):
                    continue
                bt = b.get('type')
                if bt == 'text':
                    parts.append('TEXT[' + str(len(b.get('text', ''))) + ']:' + b.get('text', ''))
                elif bt == 'tool_use':
                    parts.append('TOOL_USE:' + b.get('name', '') + ':' + json.dumps(b.get('input', {}), ensure_ascii=False))
                elif bt == 'tool_result':
                    cc = b.get('content')
                    if isinstance(cc, list):
                        s = ' '.join((x.get('text', '') if isinstance(x, dict) else '') for x in cc)
                    else:
                        s = str(cc)
                    parts.append('TOOL_RESULT[' + str(len(s)) + ']:' + s)
                elif bt == 'thinking':
                    parts.append('THINK[' + str(len(b.get('thinking', ''))) + ']')
                else:
                    parts.append(bt or '?')
            summ = ' | '.join(parts)
        rows.append((i, t, ts, summ))

mode = sys.argv[2] if len(sys.argv) > 2 else 'users'
if mode == 'users':
    for i, t, ts, s in rows:
        if t == 'user' and (s.startswith('STR:') or 'TEXT[' in s):
            print(i, ts, s[:int(sys.argv[3]) if len(sys.argv) > 3 else 800])
            print('---')
elif mode == 'types':
    from collections import Counter
    print(Counter(r[1] for r in rows))
    print('first ts', rows[0][2], 'last ts', rows[-1][2])
elif mode == 'range':
    a = int(sys.argv[3]); b = int(sys.argv[4]); n = int(sys.argv[5]) if len(sys.argv) > 5 else 1500
    for i, t, ts, s in rows[a:b + 1]:
        print(i, t, ts, s[:n])
        print('---')
