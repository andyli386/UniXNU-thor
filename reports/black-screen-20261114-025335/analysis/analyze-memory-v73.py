#!/usr/bin/env python3
"""Read-only analysis of exported logs; keep literal timestamps and replay dedup."""
from pathlib import Path
import collections
import json
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / 'THOR-v72-010642'
DEC = json.JSONDecoder()

def parse(root):
    p = root / 'extracted/files/logs/PongConsole.txt'
    raw = p.read_bytes()
    seen = set()
    unique = []
    malformed = []
    lines = raw.decode(errors='replace').splitlines()
    for number, line in enumerate(lines, 1):
        if line in seen or not line.strip('\x00 \t'):
            continue
        seen.add(line)
        m = re.match(r'\[\s*(\d+)\.(\d+)\]:', line)
        item = {'line': number, 'text': line}
        if m:
            item['raw_literal'] = m.group(1) + '.' + m.group(2)
            item['raw_second'] = int(m.group(1))
            item['timestamp_standard'] = len(m.group(2)) == 6
            item['raw'] = float(item['raw_literal']) if item['timestamp_standard'] else None
            if not item['timestamp_standard']:
                malformed.append(item)
        unique.append(item)
    timed = [x for x in unique if x.get('raw') is not None]
    ram = next(x for x in timed if 'Pong RAM:' in x['text'])
    for x in unique:
        if x.get('raw') is not None:
            x['since_ram'] = x['raw'] - ram['raw']
    kills = []
    samples = []
    for x in unique:
        m = re.search(r'memorystatus: (killing\w*) pid (\d+) \[([^\]]+)\] \(([^ )]+)', x['text'])
        if m:
            kills.append({**x, 'function': m.group(1), 'pid': int(m.group(2)),
                          'name': m.group(3), 'reason': m.group(4)})
        m = re.search(r'memorystatus_available_pages:\s*(\d+)\s+compressor_size:\s*(\d+)', x['text'])
        if m:
            samples.append({**x, 'available_pages': int(m.group(1)),
                            'compressor_pages': int(m.group(2)),
                            'available_mib': int(m.group(1)) / 256,
                            'compressor_mib': int(m.group(2)) / 256})
    native = []
    for p in sorted((root / 'extracted/files').rglob('*.ips')):
        text = p.read_text(errors='replace')
        record = {'file': str(p.relative_to(root / 'extracted/files')), 'bytes': p.stat().st_size}
        try:
            record['header'], pos = DEC.raw_decode(text)
            record['header_chars'] = pos
        except ValueError as e:
            record['header_error'] = str(e)
        m = re.search(r'"memoryStatus"\s*:\s*', text)
        if m:
            try:
                mem, _ = DEC.raw_decode(text[m.end():])
                record['memoryStatus'] = mem
                page = mem.get('pageSize', 4096)
                pages = mem.get('memoryPages', {})
                record['mib'] = {k: v * page / 1048576 for k, v in pages.items() if isinstance(v, (int, float))}
                record['compressor_mib'] = mem.get('compressorSize', 0) * page / 1048576
                record['active_plus_inactive_mib'] = (pages.get('active', 0) + pages.get('inactive', 0)) * page / 1048576
            except ValueError as e:
                record['memory_error'] = str(e)
        native.append(record)
    diag = (root / 'extracted/files/logs/PongDiag.txt').read_text(errors='replace').splitlines()
    windows = []
    for i, s in enumerate(diag, 1):
        m = re.search(r'^\[(\d+)\] Pong cache pre-read: .*?read=(\d+) of (\d+) bytes ms=(\d+) (.*)', s)
        if m:
            windows.append({'line': i, 'epoch': int(m.group(1)), 'bytes': int(m.group(2)),
                            'target': int(m.group(3)), 'ms': int(m.group(4)), 'text': s})
    diag_times = [int(m.group(1)) for s in diag if (m := re.match(r'^\[(\d+)\]', s))]
    clears = [x for x in unique if 'layers EMPTIED:' in x['text']]
    if not clears:
        clears = [x for x in unique if 'surfaces in layers 0x0 now' in x['text']]
    return {'root': str(root), 'console_bytes': len(raw), 'console_lines': len(lines),
            'unique_nonempty': len(unique), 'literal_duplicate_lines': len(lines) - len(seen),
            'ram': ram, 'modes': [x for x in unique if 'vm_compressor_mode' in x['text'] or 'VM swap disabled' in x['text']],
            'raw_bounds': [min(x['raw'] for x in timed), max(x['raw'] for x in timed)],
            'raw_duration_since_ram': max(x['raw'] for x in timed) - ram['raw'],
            'last_timed': max(timed, key=lambda x: x['raw']), 'malformed_timestamps': malformed,
            'kill_events': kills, 'kill_counts': dict(collections.Counter(x['reason'] for x in kills)),
            'kernel_memory_samples': samples,
            'first_softlimit': next((x for x in samples if 'enforcing soft memory limits' in x['text']), None),
            'clear_events': clears,
            'native_reports': native, 'diag_epoch_bounds': [min(diag_times), max(diag_times)],
            'prefetch': {'windows': windows, 'count': len(windows), 'mib': sum(x['bytes'] for x in windows) / 1048576,
                         'read_ms_total': sum(x['ms'] for x in windows),
                         'epoch_bounds': [min(x['epoch'] for x in windows), max(x['epoch'] for x in windows)] if windows else [],
                         'deferred_lines': [s for s in diag if 'pre-read' in s and ('defer' in s.lower() or 'gate-fail' in s.lower())],
                         'clamped_complete_lines': [s for s in diag if 'pre-read' in s and 'clamped to EOF complete gate-ok' in s]},
            'audio_memory_kills': [x for x in kills if x['name'] in ['audiomxd', 'mediaremoted', 'SpringBoard', 'backboardd']],
            'audio_console_lines': [x for x in unique if 'audiomxd' in x['text']],
            'apfs_after_clear': [x for x in timed if clears and 'tx_flush:' in x['text'] and x['raw'] > clears[0]['raw']]}

current = parse(ROOT)
baseline = parse(BASE)
duration = min(current['raw_duration_since_ram'], baseline['raw_duration_since_ram'])
equal = {'duration_since_ram': duration, 'not_causal_ab': True}
for label, data in [('v72', baseline), ('v73', current)]:
    cutoff = data['ram']['raw'] + duration
    selected = [x for x in data['kill_events'] if x.get('raw') is not None and x['raw'] <= cutoff]
    ambiguous = [x for x in data['kill_events'] if x.get('raw') is None]
    # A malformed fraction is kept literal. Count only if its integer seconds are safely inside the window.
    within = [x for x in ambiguous if data['ram']['raw'] + 2 <= x.get('raw_second', -1) <= cutoff - 2]
    outside = [x for x in ambiguous if x.get('raw_second', -1) > cutoff + 2 or x.get('raw_second', -1) < data['ram']['raw'] - 2]
    boundary = [x for x in ambiguous if x not in within and x not in outside]
    selected += within
    equal[label] = {'raw_ram_anchor': data['ram']['raw'], 'raw_cutoff': cutoff,
                    'kill_count': len(selected), 'reason_counts': dict(collections.Counter(x['reason'] for x in selected)),
                    'nonstandard_fraction_inside_window': len(within), 'nonstandard_fraction_outside_window': len(outside),
                    'ambiguous_boundary_events': boundary}
current['equal_kernel_window'] = equal
current['baseline_summary'] = {k: baseline[k] for k in ['ram', 'raw_bounds', 'raw_duration_since_ram', 'clear_events', 'kill_counts', 'prefetch']}
current['method'] = ('Whole identical Console lines deduplicated to remove ring replay. Raw kernel timestamps, '
                     'PongDiag epoch and native report timestamps are separate time domains. Non-six-digit fractions '
                     'are preserved; not normalized for exact timing. Equal-window counts use RAM marker anchor; '
                     'malformed fractions are counted only when their integer seconds are safely within both boundaries. '
                     'Native memoryStatus objects are decoded individually even if the containing IPS is incomplete. '
                     'No timed experiment and no new hardware benchmark was run.')
(ROOT / 'analysis/v73-memory-latency.json').write_text(json.dumps(current, indent=2, ensure_ascii=False) + '\n')
brief = {k: current[k] for k in ['console_bytes', 'console_lines', 'unique_nonempty', 'ram', 'raw_bounds',
                               'raw_duration_since_ram', 'kill_counts', 'first_softlimit', 'equal_kernel_window', 'audio_memory_kills']}
brief['malformed_timestamp_count'] = len(current['malformed_timestamps'])
brief['prefetch_summary'] = {k: v for k, v in current['prefetch'].items() if k != 'windows'}
brief['native_memory'] = [{k: v for k, v in x.items() if k in ['file', 'header', 'memoryStatus', 'mib', 'compressor_mib', 'active_plus_inactive_mib']} for x in current['native_reports']]
print(json.dumps(brief, indent=2, ensure_ascii=False))
