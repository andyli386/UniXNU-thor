#!/usr/bin/env python3
"""Independent, offline MEM08 release gate. No device commands or entropy output.

The immutable MEM07 verifier supplies bounded parsers and the historical budget;
MEM08 geometry, native continuation, FULL08 decoding and wire format are checked
here independently. A report never authorizes or executes a phone boot.
"""
import argparse
import datetime
import functools
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import tomllib
import zlib
from pathlib import Path


class GateError(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise GateError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def align(value, size=4096):
    return (value + size - 1) // size * size


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def prior_module():
    return load_module('mem08_frozen_mem07_helpers', Path(__file__).with_name('verify_mem07_candidate.py'))


def rooted(root, value):
    value = Path(value)
    path = (root / value).resolve()
    check(path.is_relative_to(root), 'input path stays inside project root')
    return path


def pin(path, expected, message):
    check(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected), message + ': digest syntax')
    check(Path(path).is_file() and sha(path) == expected, message)


def same_document(first, second):
    return {k: v for k, v in first.items() if k not in {'created_at', 'checked_at'}} == {
        k: v for k, v in second.items() if k not in {'created_at', 'checked_at'}}


class Requirements:
    def __init__(self):
        self.marks = []

    def __call__(self, condition, message):
        self.marks.append({'contract': message, 'passed': bool(condition)})
        check(condition, message)


FULL_MAGIC = b'THOR-FULL08' + b'\0' * 5
FULL_HEADER = struct.Struct('<16sIIQ32sQQ32sIIQ')
RAW_SHA = 'a798cf368ab013e7389f3c5944cea9381859fb98238b346b33423b00bf8d3a37'
RAW_BYTES = 50659328
FULL_SPAN = 113229824
FULL_SPAN_SHA = '87eaa5e88ba9ea4d1b36cf18734ad634a7dc29f81caeb109107e50476722927d'
WINDOW_BYTES = 112 << 20
MINIMUM_LAYOUT = FULL_SPAN + 0x5000
SCRATCH_OFFSET = MINIMUM_LAYOUT
SCRATCH_RESERVED = 0x4000
MEM07_IMAGE = 'fea3b5d12d9908170229f232cfa20bea5769415a2187a0ff9b622f55987da55e'
MEM07_KERNEL = 'd52a98872ad3fa2ddb24c00c2741e21ff62b69836be7eb996f32afdcbb2eed08'
MEM07_MC = 'f9258c391883905ffe6b7742299bb5cf41b2eababbb3b69986d1157634bd8b8d'
MEM07_HEAD = 'aaf0d8b41197b0bfc2772f078f316e1370478223'
MEM07_TRANSITION = 'de0b5fd081759dde1f579f7741c2a6cab40feea95285e5c9eaee9832a87da195'
APPLE_XNU = 'd8b80295118ef25ac3a784134bcf95cd8e88109f'
FB_START, FB_END, FB_PAGES = 0xb805a000, 0xb81e1000, 391
FB_FLAGS = 0x006000000000070b
GOP_BASE, GOP_BYTES = 0xb8000000, 1440 * 3200 * 4


def full08_header_contract(header, compressed):
    check(len(header) == FULL_HEADER.size == 128, 'FULL08 header is exactly 128 bytes')
    magic, version, size, n, compressed_sha, raw_n, span, raw_sha, algorithm, count, reserved = FULL_HEADER.unpack(header)
    check(magic == FULL_MAGIC and version == 1 and size == 128, 'FULL08 unique wire identity and version')
    check(0 < n < 32 << 20 and n == len(compressed), 'FULL08 exact bounded compressed length')
    check(compressed_sha.hex() == sha_bytes(compressed), 'FULL08 compressed bytes hash matches header')
    check(raw_n == RAW_BYTES and span == FULL_SPAN and raw_sha.hex() == RAW_SHA,
          'FULL08 pins the actual complete APFS kernel and its loaded span')
    check(algorithm == 1 and count == 15 and reserved == 0,
          'FULL08 EFI standard algorithm, 15 nonzero segments and zero reserved field')
    return {'compressed_bytes': n, 'compressed_sha256': compressed_sha.hex(), 'raw_bytes': raw_n,
            'raw_sha256': raw_sha.hex(), 'loaded_span_bytes': span, 'algorithm_id': algorithm,
            'nonzero_segment_count': count, 'header_sha256': sha_bytes(header)}


def full_macho_plan(data):
    """Parse every LC_SEGMENT_64 record, including all zero-size records."""
    check(len(data) >= 32, 'full Mach-O header present')
    magic, cpu, _, _, ncmds, cmdbytes, _, _ = struct.unpack_from('<8I', data)
    check(magic == 0xfeedfacf and cpu == 0x100000c, 'full raw kernel is ARM64 Mach-O')
    check(0 < ncmds <= 4096 and 32 + cmdbytes <= len(data), 'full load-command table bounded')
    cursor, segments = 32, []
    for _ in range(ncmds):
        check(cursor + 8 <= 32 + cmdbytes, 'full load command header present')
        kind, size = struct.unpack_from('<II', data, cursor)
        check(size >= 8 and size % 8 == 0 and cursor + size <= 32 + cmdbytes,
              'full load command stays within table')
        if kind == 0x19:
            check(size >= 72, 'full LC_SEGMENT_64 fixed fields present')
            name = data[cursor + 8:cursor + 24].split(b'\0', 1)[0].decode('ascii')
            va, virtual_bytes, offset, file_bytes = struct.unpack_from('<QQQQ', data, cursor + 24)
            maxprot, initprot, sections, flags = struct.unpack_from('<4I', data, cursor + 56)
            check(size == 72 + sections * 80, 'full segment section array exactly covers command')
            check(file_bytes <= virtual_bytes and offset <= len(data) and file_bytes <= len(data) - offset,
                  'full segment file range lies inside authenticated raw input')
            check(va <= (1 << 64) - virtual_bytes, 'full segment VA addition does not overflow')
            check(virtual_bytes or file_bytes == 0, 'zero-sized segment has no file contents')
            segments.append({'name': name, 'va': va, 'vm_bytes': virtual_bytes,
                'file_offset': offset, 'file_bytes': file_bytes, 'maxprot': maxprot,
                'initprot': initprot, 'sections': sections, 'flags': flags})
        cursor += size
    check(cursor == 32 + cmdbytes, 'full load commands exactly consumed')
    nonzero = sorted((s for s in segments if s['vm_bytes']), key=lambda s: s['va'])
    check(nonzero, 'full loaded segment list is not empty')
    base = nonzero[0]['va']
    end = max(s['va'] + s['vm_bytes'] for s in nonzero)
    check(0 < end - base <= WINDOW_BYTES, 'full span fits within separately owned 112MiB window')
    for segment in nonzero:
        segment['destination_offset'] = segment['va'] - base
        check(segment['destination_offset'] >= segment['file_offset'],
              'reverse in-place expansion never moves a segment below its source')
    check(all(a['va'] + a['vm_bytes'] <= b['va'] for a, b in zip(nonzero, nonzero[1:])),
          'full loaded segments do not overlap')
    file_backed = [segment for segment in nonzero if segment['file_bytes']]
    check(all(a['file_offset'] + a['file_bytes'] <= b['file_offset']
              for a, b in zip(file_backed, file_backed[1:])),
          'full file ranges share the same nonoverlapping order as loaded VAs')
    loaded = bytearray(end - base)
    for s in nonzero:
        start = s['destination_offset']
        loaded[start:start + s['file_bytes']] = data[s['file_offset']:s['file_offset'] + s['file_bytes']]
    return {'segments': segments, 'sorted_nonzero_segments': nonzero, 'segment_record_count': len(segments),
            'nonzero_segment_count': len(nonzero), 'virtual_base': base, 'virtual_end': end,
            'loaded_span_bytes': len(loaded), 'loaded_span_sha256': sha_bytes(loaded)}


def full_profile_contract(plan):
    check(plan['segment_record_count'] == 18 and plan['nonzero_segment_count'] == 15,
          'actual APFS kernel has 18 segment records and exactly 15 loaded segments')
    check(plan['loaded_span_bytes'] == FULL_SPAN and plan['loaded_span_sha256'] == FULL_SPAN_SHA,
          'independently reconstructed full loaded bytes have pinned span and SHA256')


def scratch_contract(manifest):
    check(manifest['window_bytes'] == WINDOW_BYTES and manifest['minimum_layout_bytes'] == MINIMUM_LAYOUT,
          'FULL08 owned window and minimum layout match immutable geometry')
    check(manifest['scratch_offset'] == SCRATCH_OFFSET and manifest['scratch_reserved_bytes'] == SCRATCH_RESERVED,
          'FULL08 codec workspace lies after loaded span plus 0x5000 handoff isolation')
    check(type(manifest['scratch_bytes']) is int and 0 < manifest['scratch_bytes'] <= SCRATCH_RESERVED,
          'production decoder measured scratch fits the explicit 16KiB reservation')
    check(SCRATCH_OFFSET >= FULL_SPAN and SCRATCH_OFFSET + SCRATCH_RESERVED <= WINDOW_BYTES,
          'codec workspace cannot alias raw/expanded kernel and stays within owned window')


def coverage_contract(producer_pages, consumer_pages, start, end, flags):
    check((producer_pages, consumer_pages, start, end, flags) == (391, 391, FB_START, FB_END, FB_FLAGS),
          'MEM08 producer/consumer share the exact 391-page framebuffer aperture')
    check(end - start == producer_pages * 4096 and 90 + producer_pages == 481 <= 512,
          'MEM08 identity leaves remain in the existing bootstrap L3')
    check(GOP_BASE <= start < end <= GOP_BASE + GOP_BYTES, 'MEM08 framebuffer extent remains inside original GOP memory')
    return [start + i * 4096 for i in range(producer_pages)]


def geometry_contract(geometry):
    check(geometry['rows'] == geometry['columns'] == 4, 'MEM08 grid remains four by four')
    check(geometry['fb_existing_owned_pages'] == 391 and geometry['new_page_table_allocations'] == 0,
          'MEM08 grid uses 391 existing-GOP pages and no new page tables')
    check(geometry['new_high_alias_L3_indices'] == [16, 406] and geometry['old_hw07_alias_L3_indices'] == [480, 499],
          'MEM08 391 aliases and legacy twenty aliases remain disjoint in one L3')
    check(geometry['tile_width_pixels'] == 328 and geometry['tile_height_pixels'] == 42,
          'MEM08 tile dimensions retain readable two-line text')
    cells = geometry['cells']
    check(len(cells) == 16, 'MEM08 geometry has sixteen cells')
    rectangles = []
    for i, cell in enumerate(cells):
        rect = [16 + 352 * (i & 3), 62 + 54 * (i >> 2), 328, 42]
        check(cell['slot'] == i and cell['relative_xywh'] == rect,
              'MEM08 every grid tile is shifted exactly 54 physical scanlines')
        x, y, width, height = rect
        check(0 <= x < x + width <= 1440 and y + height <= 278,
              'MEM08 all tile pixels remain in expanded logical canvas')
        address = FB_START + (y * 1440 + x) * 4
        check(cell['physical_first_pixel'] == hex(address), 'MEM08 cell has correct physical first pixel')
        old_address = FB_START + ((y - 54) * 1440 + x) * 4
        check(address - old_address == 54 * 1440 * 4, 'MEM08 physical displacement is one old row pitch')
        check(FB_START <= address and FB_START + ((y + height - 1) * 1440 + x + width) * 4 <= FB_END,
              'MEM08 last tile pixel stays in authenticated framebuffer mapping')
        check(not any(prior_module().rectangles_intersect(rect, old) for old in rectangles),
              'MEM08 grid tiles do not overlap')
        rectangles.append(rect)


def mem08_pe_identity(binary):
    return all(text in binary for text in (b'RUN MEM08 / THOR SM8475', b'THOR FDT MEMDIAG 08',
        b'MEM08 FDT WINDOW ONLY - NO MEMORY GRANT')) and not any(
        text in binary for text in (b'RUN MEM07 / THOR SM8475', b'THOR FDT MEMDIAG 07'))


def numbered_display_contract(palette, labels, states, font):
    check(len(palette) == len(labels) == len(states) == 16 and len(set(palette)) == 16,
          'MEM08 sixteen distinct checkpoint cells and colors')
    check(all(type(c) is int and 0 <= c <= 0xffffff and c != 0x202020 for c in palette),
          'MEM08 actual RGB palette excludes inactive-state sentinel')
    check(labels[11:] == ['PMAP CALL', 'PMAP RETURN', 'PMAP GUARD', 'L3 CALL', 'BOOT LOG'],
          'MEM08 final slot names the completed real logging frontier')
    check(states[7:9] == ['CALL', 'RETURN'] and states[11:] == ['CALL', 'RETURN', 'PASS', 'CALL', 'PASS'],
          'MEM08 retained call/return terminal states are exact')
    check(all(isinstance(s, str) and s.isascii() and 0 < len(s) < 16 for s in labels),
          'MEM08 full checkpoint labels fit their fixed cells')
    check(len(font) == 252 and all(type(v) is int and 0 <= v < 32 for v in font),
          'MEM08 exact thirty-six-glyph font dimensions')
    check(len({tuple(font[i * 7:i * 7 + 7]) for i in range(16)}) == 16,
          'MEM08 hexadecimal identifiers are unique')


def transition_words_contract(old, new, layout):
    check(len(old) == len(new) == 500 and sha_bytes(old) == MEM07_TRANSITION,
          'MEM08 transition starts from exact immutable MEM07 raw bytes')
    allowed = {76, 80, 96, 100, 116, 120, 136, 140, 156, 160}
    changed = [i for i in range(0, 500, 4) if old[i:i + 4] != new[i:i + 4]]
    check(set(changed) <= allowed, 'MEM08 only independently identified layout-immediate words may change')
    for i in changed:
        a, b = struct.unpack_from('<I', old, i)[0], struct.unpack_from('<I', new, i)[0]
        check(a & ~(0xffff << 5) == b & ~(0xffff << 5),
              'MEM08 transition opcode, width, register, shift and branch bits unchanged')
    def pair(data, offset, reg):
        a, b = struct.unpack_from('<II', data, offset)
        check(a & 0x7f80001f == 0x52800000 | reg and b & 0x7f80001f == 0x72800000 | reg
              and (a >> 21) & 3 == 0 and (b >> 21) & 3 == 1 and a >> 31 == b >> 31,
              'MEM08 transition MOVZ/MOVK pair has exact registers and shifts')
        return (a >> 5) & 65535 | (((b >> 5) & 65535) << 16)
    old_values = {'transition': 20201472 + 0x4000, 'args': 20201472,
        'entry': 0x400488, 'vector': 0x401000, 'stack_top': 20201472 + 0x3c000}
    expected = {'transition': (76, 9, layout['loaded_span_bytes'] + 0x4000),
        'args': (96, 8, layout['loaded_span_bytes']), 'entry': (116, 8, layout['entry_offset']),
        'vector': (136, 8, layout['vectors_offset']), 'stack_top': (156, 8, layout['loaded_span_bytes'] + 0x3c000)}
    values = {}
    for name, (offset, reg, value) in expected.items():
        check(type(value) is int and 0 < value < 64 << 20 and pair(old, offset, reg) == old_values[name]
              and pair(new, offset, reg) == value, 'MEM08 transition independently encodes actual ' + name)
        values['old_decoded_' + name + '_offset'] = old_values[name]
        values['new_decoded_' + name + '_offset'] = value
    return dict(changed_instruction_word_offsets=changed, unchanged_bytes=500 - 4 * len(changed), **values)


def full08_bundle_contract(bundle, kernel, compressed, header, seed_digest):
    check(bundle[:16] == b'THOR-PAYLOAD09\0\0', 'MEM08 retains primary payload09 transport identity')
    version, prefix, count = struct.unpack_from('<IIQ', bundle, 16)
    check((version, prefix, count) == (2, 128, len(kernel)) and len(kernel) < 32 << 20,
          'MEM08 primary diagnostic payload remains separately bounded below 32MiB')
    check(bundle[32:64].hex() == sha_bytes(kernel) and bundle[128:128 + count] == kernel,
          'MEM08 authenticated primary kernel bytes are exact')
    check(len(bundle[64:128]) == 64 and any(bundle[64:128]) and sha_bytes(bundle[64:128]) == seed_digest,
          'MEM08 one-use seed digest matches without exposing entropy')
    pos = align(max(128 + count, 19289048))
    check(not any(bundle[128 + count:pos]), 'MEM08 primary padding is all zero')
    check(len(bundle) < 64 << 20 and bundle.count(FULL_MAGIC) == 1,
          'MEM08 sole FULL08 envelope lies in bounded initrd read domain')
    end = pos + 128 + len(compressed)
    check(bundle[pos:pos + 128] == header and bundle[pos + 128:end] == compressed,
          'MEM08 appended FULL08 header and compressed asset are exact')
    check(len(bundle) == align(end) and not any(bundle[end:]), 'MEM08 aux tail has exact zero page padding')
    full08_header_contract(header, compressed)
    return {'full08_header_offset': pos, 'full08_blob_offset': pos + 128, 'full08_end': end,
            'bundle_bytes': len(bundle), 'seed_logged': False}


LOG_BASELINE_TUS = ['libkern/os/log.c', 'libkern/os/log_queue.c', 'osfmk/kern/printf.c',
                    'bsd/kern/subr_log.c', 'osfmk/arm/rtclock.c', 'bsd/kern/subr_log_stream.c']
LOG_RETAINED_TUS = ['libkern/os/log_encode.c', 'osfmk/kern/clock.c',
                    'bsd/kern/subr_prf.c', 'osfmk/kern/lock_ticket.c']


def strip_mem08_observers(text):
    lines = text.splitlines(keepends=True); result = []; i = 0
    marker = '#if defined(XNU_THOR_EARLY_C) && XNU_THOR_EARLY_C'
    while i < len(lines):
        if lines[i].strip() != marker:
            result.append(lines[i]); i += 1; continue
        start, depth = i, 1; i += 1
        while i < len(lines) and depth:
            token = lines[i].lstrip()
            if re.match(r'#\s*(?:if|ifdef|ifndef)\b', token): depth += 1
            elif re.match(r'#\s*endif\b', token): depth -= 1
            i += 1
        check(depth == 0, 'MEM08 observer preprocessor block is balanced')
        block = ''.join(lines[start:i])
        if 'thor_mem08_' not in block:
            result.append(block)
    return ''.join(result)


def reconstruct_mem07_log_baseline(root, xsrc):
    paths = LOG_BASELINE_TUS + LOG_RETAINED_TUS
    prior = root / 'worktrees/xnu/vm-mem07'
    reference = prior / 'refer/xnu'
    env = os.environ.copy(); env['GIT_NO_LAZY_FETCH'] = '1'
    files = subprocess.check_output(['git', '-C', str(prior), 'ls-tree', '-r', '--name-only', MEM07_HEAD,
                                    '--', 'patches/xnu'], text=True, env=env).splitlines()
    patches = sorted(p for p in files if p.endswith('.patch'))
    check(patches, 'fixed MEM07 source revision has ordered XNU patch inputs')
    inputs, source_inputs = {}, {}
    with tempfile.TemporaryDirectory(prefix='mem08-independent-log-baseline-') as temp:
        temp = Path(temp)
        for name in paths:
            data = subprocess.check_output(['git', '-C', str(reference), 'show', APPLE_XNU + ':' + name], env=env)
            inputs[APPLE_XNU + ':' + name] = sha_bytes(data)
            check((reference / name).read_bytes() == data, 'fixed original Apple working copy equals Gitblob: ' + name)
            source_inputs[str(reference / name)] = sha_bytes(data)
            path = temp / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        for index, name in enumerate(patches):
            data = subprocess.check_output(['git', '-C', str(prior), 'show', MEM07_HEAD + ':' + name], env=env)
            inputs[MEM07_HEAD + ':' + name] = sha_bytes(data)
            check((prior / name).read_bytes() == data, 'fixed MEM07 patch working copy equals Gitblob: ' + name)
            source_inputs[str(prior / name)] = sha_bytes(data)
            patch = temp / ('patch-%03d.diff' % index); patch.write_bytes(data)
            result = subprocess.run(['git', 'apply', *['--include=' + n for n in paths], str(patch)],
                cwd=temp, env=env, capture_output=True, text=True)
            check(result.returncode == 0, 'fixed ordered MEM07 patch reconstructs original log sources: ' + Path(name).name)
        outputs = {}
        for name in paths:
            data = (temp / name).read_bytes(); outputs[name] = sha_bytes(data)
            check((prior / '.cache/xnu-work' / name).read_bytes() == data,
                  'independent fixed-Git reconstruction matches MEM07 prepared log TU: ' + name)
            current = (xsrc / '.cache/xnu-work' / name).read_text()
            if name in LOG_BASELINE_TUS:
                check((xsrc / '.cache/mem08-baseline' / name).read_bytes() == data,
                      'MEM08 frozen baseline log TU matches independent MEM07 reconstruction: ' + name)
                clean = strip_mem08_observers(current)
                normalized = lambda text: '\n'.join(line for line in text.splitlines() if line.strip())
                check(normalized(clean) == normalized(data.decode()),
                      'MEM08 native source unchanged except observer blocks and blank separator lines: ' + name)
                source_inputs[str(xsrc / '.cache/mem08-baseline' / name)] = sha_bytes(data)
            else:
                check(current.encode() == data, 'retained first-log callee TU remains exact fixed-Git native algorithm: ' + name)
                source_inputs[str(xsrc / '.cache/xnu-work' / name)] = sha_bytes(data)
            source_inputs[str(prior / '.cache/xnu-work' / name)] = sha_bytes(data)
    return {'apple_revision': APPLE_XNU, 'MEM07_revision': MEM07_HEAD, 'ordered_patch_count': len(patches),
            'fixed_git_input_sha256': inputs, 'reconstructed_source_sha256': outputs,
            'input_file_sha256': source_inputs}


def check_regression_receipts(root, xsrc, built, require):
    details = built['regression_tests']['details']
    mandatory = {'test_thor_mem08_log.py', 'test_thor_mem08_render.py',
                 'test_thor_mem05_budget.py', 'test_thor_mem05_cfg.py'}
    require(mandatory <= set(details), 'MEM08 fresh log/render and historical budget/CFG test receipts are complete')
    fresh = reused = 0
    for name, item in details.items():
        require(item['returncode'] == 0 and type(item['passed']) is int and item['passed'] > 0,
                'successful declared native regression: ' + name)
        pin(xsrc / 'tests' / name, item['source_sha256'], 'same native test source: ' + name)
        path = rooted(root, item['log_path']); pin(path, item['log_sha256'], 'same completed regression log: ' + name)
        require(re.search(r'Ran ' + str(item['passed']) + r' tests', path.read_text()) is not None,
                'actual unittest count agrees with receipt: ' + name)
        if item['reexecuted']:
            fresh += item['passed']
        else:
            require(name in {'test_thor_mem05_budget.py', 'test_thor_mem05_cfg.py'},
                    'only unchanged historical budget and CFG tests may be reused')
            reused += item['passed']
    require(reused == 20 and built['regression_tests']['baseline_reused_count'] == reused
            and built['regression_tests']['fresh_count'] == fresh
            and built['regression_tests']['passed'] == fresh + reused and built['regression_tests']['failed'] == 0,
            'MEM08 native regression counts distinguish fresh checks from twenty historical reuses')
    return {'fresh_tests': fresh, 'unchanged_tests_reused': reused}


@functools.lru_cache(maxsize=1)
def frozen_native_helpers(root):
    """Load the already reviewed MEM07 integer engine, never the new auditor."""
    tree = root / 'worktrees/xnu/vm-mem07'
    check(subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True).strip() == MEM07_HEAD,
          'independent bootstrap engine belongs to frozen MEM07 revision')
    path = tree / 'scripts/audit-thor-mem07-linked.py'
    module = load_module('mem08_immutable_integer_engine', path)
    seen, files = set(), {}
    def visit(item):
        if id(item) in seen:
            return
        seen.add(id(item))
        filename = getattr(item, '__file__', None)
        if not filename:
            return
        p = Path(filename).resolve()
        if not p.is_relative_to(tree) or p.suffix != '.py':
            return
        relative = str(p.relative_to(tree))
        original = subprocess.check_output(['git', '-C', str(tree), 'show', MEM07_HEAD + ':' + relative])
        check(p.read_bytes() == original, 'fixed historical audit helper exact Gitblob: ' + relative)
        files[str(p)] = sha(p)
        for value in vars(item).values():
            if isinstance(value, type(sys)):
                visit(value)
    visit(module)
    check(len(files) >= 6, 'historical bootstrap/CFG helper import closure is inventoried')
    return module, files


def validate_bootstrap_machine(engine, machine):
    coverage_contract(391, 391, FB_START, FB_END, FB_FLAGS)
    for index in range(FB_PAGES):
        pa = FB_START + index * 4096
        result = engine.walk(machine, pa)
        check(result is not None and result[0] == pa and result[1] & ~engine.PA_MASK == FB_FLAGS,
              'actual integer bootstrap maps every MEM08 framebuffer page with exact attributes')
    check(all(engine.walk(machine, pa) is None for pa in (FB_START - 4096, FB_END)),
          'actual bootstrap leaves framebuffer neighbors unmapped')
    table = engine.POOL + 9 * engine.PAGE
    live = [i for i in range(512) if machine.mem[table + i * 8] & 1]
    check(live == list(range(90, 481)), 'actual framebuffer bootstrap L3 contains exactly leaves 90 through 480')
    for offset in (0, engine.OWNED_BYTES - 1):
        check(engine.walk(machine, engine.OWNED + offset)[0] == engine.OWNED + offset,
              'actual expanded framebuffer mapping preserves owned RAM identity edge')
        check(engine.walk(machine, engine.KVA + offset, engine.POOL + engine.PAGE)[0] == engine.OWNED + offset,
              'actual expanded framebuffer mapping preserves owned RAM KVA edge')
    return {'producer_pages': 391, 'consumer_pages': 391, 'physical_range': [hex(FB_START), hex(FB_END)],
            'bootstrap_L3_indices': [90, 480], 'executed_integer_instructions': machine.steps,
            'actual_table_stores': len(machine.writes)}


def independent_alias_replay(engine, rows):
    tlbis = [i for i, (_, s) in enumerate(rows) if 'tlbi' in s and 'vmalle1is' in s]
    check(len(tlbis) == 1, 'actual MEM08 alias installer has one final TLBI')
    tlbi = tlbis[0]
    first = next((i for i, (_, s) in enumerate(rows[:tlbi])
                  if re.search(r'\bstr\s+x\d+,\s*\[x\d+,\s*#0xf00\]', s)), None)
    check(first is not None, 'actual MEM08 retains the first legacy alias store')
    guards = [i for i in range(first) if rows[i][1].startswith('cbnz')]
    check(guards, 'alias block has a prior reject-on-nonzero table guard')
    begin, end = max(guards) + 1, tlbi - 1
    check(begin < first and rows[end][1] == 'dsb\tishst' and rows[tlbi + 1][1] == 'dsb\tish'
          and rows[tlbi + 2][1] == 'isb', 'actual MEM08 alias stores preserve DSB/TLBI/DSB/ISB order')
    block = list(rows[begin:end]) + [(rows[end][0], 'ret')]
    match = re.search(r'\[(x\d+),', rows[first][1])
    check(match is not None, 'actual high-alias table base register is known')
    register = int(match.group(1)[1:]); table = engine.POOL + 10 * engine.PAGE
    machine = engine.BootstrapMachine(block); machine.require_zero = False
    machine.mem = {a: 0 for a in machine.mem}; machine.reg[register] = table
    if register in machine.saved:
        machine.saved[register] = table
    saved = dict(machine.saved); machine.saved = {}; machine.run()
    changed = [reg for reg, value in saved.items() if machine.reg[reg] != value]
    saves, restores = {}, set()
    for _, line in rows[:begin]:
        m = re.match(r'stp\s+(x\d+),\s*(x\d+),\s*\[sp(?:,\s*#(-?0x[0-9a-f]+))?\](!?)', line)
        if m:
            offset = 0 if m[4] else int(m[3] or '0', 0)
            for reg in (m[1], m[2]):
                saves[int(reg[1:])] = (tuple((m[1], m[2])), offset)
    for _, line in rows[end:]:
        m = re.match(r'ldp\s+(x\d+),\s*(x\d+),\s*\[sp(?:,\s*#(-?0x[0-9a-f]+))?\]', line)
        if m:
            restores.add((tuple((m[1], m[2])), int(m[3] or '0', 0)))
    check(all(reg in saves and saves[reg] in restores for reg in changed),
          'real alias function saves/restores every modified callee-saved block register')
    for index in range(512):
        expected = (FB_START + (index - 16) * 4096 | FB_FLAGS) if 16 <= index <= 406 else (
            0xb8095000 + (index - 480) * 4096 | FB_FLAGS) if 480 <= index <= 499 else 0
        check(machine.mem[table + index * 8] == expected, 'actual MEM08 alias table matches all 512 expected descriptors')
    check(len(machine.writes) == 411, 'actual MEM08 alias installer performs exactly 391 plus twenty PTE stores')
    return {'actual_PTE_stores': 411, 'grid_L3_indices': [16, 406], 'legacy_L3_indices': [480, 499],
            'actual_store_block': [hex(block[0][0]), hex(block[-1][0])],
            'enclosing_function_stack_saved_scratch_registers': changed,
            'all_other_L3_slots_remain_zero': True}


def independent_native_mapping_replay(root, kernel, extra, require):
    module, files = frozen_native_helpers(root); engine = module.m4.m3
    producer = module.m4.old.disasm(kernel, '_thor_entry09_build_tables')
    result = validate_bootstrap_machine(engine, engine.BootstrapMachine(producer).run())
    coverage = extra['bootstrap_coverage']
    require(all(coverage[key] == value for key, value in result.items()),
            'immutable integer interpreter independently reproduces actual MEM08 bootstrap coverage')
    actual = prior_module().MachO(kernel.read_bytes())
    counters = [(i, address) for i, (address, line) in enumerate(producer)
                if re.fullmatch(r'mov\s+w16,\s*#(?:0x187|391)', line)]
    require(len(counters) == 1 and actual.movz(counters[0][1], 16) == 391,
            'raw bootstrap MOVZ w16 contains actual 391-page producer count')
    negative = {}
    for pages in (315, 390, 392):
        changed = list(producer); i, address = counters[0]
        changed[i] = (address, 'mov\tw16, #0x%x' % pages)
        try:
            validate_bootstrap_machine(engine, engine.BootstrapMachine(changed).run())
        except Exception:
            negative[str(pages)] = True
        else:
            raise GateError('independent bootstrap wrong-page mutation was accepted')
    aliases = independent_alias_replay(engine, module.m4.old.disasm(kernel, '_thor_vm_hw07_fb_prepare'))
    require(all(extra['high_alias'][key] == value for key, value in aliases.items()),
            'immutable integer interpreter independently reproduces all 411 native alias stores')
    return {'bootstrap': result, 'high_alias': aliases, 'wrong_page_counts_rejected': negative,
            'fixed_helper_input_sha256': files}


CODEC_HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "BaseUefiDecompressLibInternals.h"
static int intact(const unsigned char *p,size_t n){for(size_t i=0;i<n;i++)if(p[i]!=0xa5)return 0;return 1;}
int main(int argc,char **argv){
 if(argc!=3)return 2;
 FILE *f=fopen(argv[1],"rb");if(!f)return 3;
 if(fseek(f,0,SEEK_END))return 4;long z=ftell(f);
 if(z<8||z>20*1024*1024)return 5;rewind(f);
 unsigned char *input=malloc((size_t)z);if(!input||fread(input,1,(size_t)z,f)!=(size_t)z)return 6;fclose(f);
 UINT32 bytes=0,scratch=0;
 if(UefiDecompressGetInfo(input,(UINT32)z,&bytes,&scratch)!=0||bytes!=50659328U||scratch!=sizeof(SCRATCH_DATA)||scratch>16384U)return 7;
 unsigned char *out=malloc((size_t)bytes+64),*work=malloc((size_t)scratch+64);if(!out||!work)return 8;
 memset(out,0xa5,(size_t)bytes+64);memset(work,0xa5,(size_t)scratch+64);
 if(UefiDecompress(input,out+32,work+32)!=0)return 9;
 if(!intact(out,32)||!intact(out+32+bytes,32)||!intact(work,32)||!intact(work+32+scratch,32))return 10;
 f=fopen(argv[2],"wb");if(!f||fwrite(out+32,1,bytes,f)!=bytes||fclose(f))return 11;
 printf("{\"decoded_raw_bytes\":%u,\"scratch_bytes\":%u,\"output_canaries_intact\":true,\"scratch_canaries_intact\":true}\n",bytes,scratch);
 free(input);free(out);free(work);return 0;
}
'''


def production_codec_replay(root, asset, manifest, require, temp_parent):
    sources = manifest['source_files_sha256']
    check(isinstance(sources, dict) and len(sources) >= 10, 'FULL08 codec source and shim closure present')
    paths = {}
    for name, digest in sources.items():
        path = rooted(root, name); pin(path, digest, 'FULL08 declared codec/host-shim source: ' + path.name)
        paths[str(path)] = path
    directory = rooted(root, 'evidence/build/vm-mem08/host-codec/source')
    codec = directory / 'BaseUefiDecompressLib.c'
    internals = directory / 'BaseUefiDecompressLibInternals.h'
    pin(codec, '1eb6bcddb9ab4bafb1a0ed97ab84b5b1038350062ce883ac22876ebccf2c0bce',
        'original Mu EFI standard production decompressor algorithm bytes')
    pin(internals, '78e680ad56be7fd9bf6dac077d543c7989212a74d4ade6ce92b6d46961b040c2',
        'original Mu production decompressor internal layout bytes')
    require(manifest['host_codec']['upstream_gitlink'] == 'cfb93724a06ff86ff5ba971413368a7809ee42a5',
            'host decoder is the exact Mu source used by production firmware')
    with tempfile.TemporaryDirectory(prefix='mem08-independent-codec-', dir=temp_parent) as temporary:
        temporary = Path(temporary); harness = temporary / 'decode.c'; exe = temporary / 'decode'
        decoded = temporary / 'decoded-raw.macho'; harness.write_text(CODEC_HARNESS)
        command = ['xcrun', 'clang', '-std=c11', '-O2', '-I', str(directory), str(harness), str(codec), '-o', str(exe)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        require(result.returncode == 0 and exe.is_file(), 'freshly compile independent harness around original production decoder: ' + result.stderr[-1600:])
        result = subprocess.run([str(exe), str(asset), str(decoded)], capture_output=True, text=True, timeout=90)
        require(result.returncode == 0 and decoded.is_file(), 'original production EFI decoder executes the selected real compressed asset: rc=' + str(result.returncode) + ' ' + result.stderr[-1600:])
        summary = json.loads(result.stdout)
        require(summary['decoded_raw_bytes'] == decoded.stat().st_size == RAW_BYTES and sha(decoded) == RAW_SHA,
                'fresh production decode yields exact original APFS raw kernel SHA256')
        require(summary['scratch_bytes'] == manifest['scratch_bytes'] == 13376
                and summary['output_canaries_intact'] and summary['scratch_canaries_intact'],
                'real decoder measured scratch and both guarded buffers remain exact')
        summary.update(decoded_raw_sha256=sha(decoded), codec_source_sha256=sha(codec),
            harness_sha256=sha_bytes(CODEC_HARNESS.encode()), harness_origin='embedded in exact verifier source',
            source_files_sha256={str(path): sha(path) for path in paths.values()})
    return summary, paths


def full_loader_inputs(root, asset, manifest_path, require):
    manifest = json.loads(manifest_path.read_text())
    require(manifest['schema'] == 'thor-mem08-full-loader-asset-v1', 'MEM08 full-loader asset manifest schema')
    require(rooted(root, manifest['compressed_path']) == asset, 'selected compressed asset matches manifest path')
    header_path = rooted(root, manifest['header_path']); raw_path = rooted(root, manifest['raw_kernel_path'])
    require(header_path.parent == asset.parent == manifest_path.parent, 'one immutable sibling FULL08 public artifact')
    compressed = asset.read_bytes(); header = header_path.read_bytes(); raw = raw_path.read_bytes()
    fields = full08_header_contract(header, compressed)
    require(len(compressed) <= 20 << 20 and struct.unpack_from('<II', compressed) == (len(compressed) - 8, RAW_BYTES),
            'EFI standard inner compressed framing and raw length are exact')
    require(len(raw) == RAW_BYTES and sha_bytes(raw) == RAW_SHA, 'actual fixed full APFS raw source file rehashed')
    plan = full_macho_plan(raw); full_profile_contract(plan); scratch_contract(manifest)
    require(True, 'independent full raw Mach-O reconstruction verifies all segment counts, movement order and loaded-span SHA')
    for name, value in fields.items():
        require(manifest[name] == value, 'FULL08 manifest and authenticated wire agree: ' + name)
    require(manifest['segment_record_count'] == 18 and manifest['nonzero_segment_count'] == 15
            and manifest['loaded_span_sha256'] == FULL_SPAN_SHA, 'FULL08 manifest retains separate total/nonzero record counts')
    def normalize(row):
        return dict(name=row['name'], virtual_address=row['va'], virtual_size=row['vm_bytes'],
                    file_offset=row['file_offset'], file_bytes=row['file_bytes'],
                    destination_offset=row['va'] - plan['virtual_base'] if row['vm_bytes'] else 0)
    require(manifest['segments'] == [normalize(row) for row in plan['segments']],
            'independently parsed all eighteen raw segment descriptors match asset manifest')
    require(manifest['sorted_nonzero_segments'] == [normalize(row) for row in plan['sorted_nonzero_segments']],
            'independent sorted fifteen-segment reverse expansion plan matches manifest')
    files = {str(p): p for p in (asset, header_path, raw_path, manifest_path, Path(__file__).resolve())}
    return manifest, fields, plan, files


def validate_mem08_frozen_artifact(root, candidate, xsrc, xproof, require):
    prior = prior_module(); kernel = candidate / 'kernel.macho'
    lock = json.loads((candidate / 'manifest.json').read_text())
    built = json.loads((candidate / 'build-manifest.json').read_text())
    layout = json.loads((candidate / 'kernel-layout.json').read_text()); extra = json.loads(xproof.read_text())
    require(lock['schema'] == 'thor-artifact-lock-v2' and built['schema'] == 'thor-mem08-build-manifest-v1',
            'MEM08 distinct frozen candidate and build schemas')
    require(extra['schema'] == 'thor-mem08-native-log-machinecode-v1'
            and extra['result'] == 'PASS_MEM08_NATIVE_ARM_INIT_LOG_RETURN', 'MEM08 actual native first-log machinecode identity')
    digest = sha(kernel); require(digest != MEM07_KERNEL, 'MEM08 real diagnostic kernel differs from consumed MEM07')
    require(digest == lock['kernel_sha256'] == built['kernel_sha256'] == layout['kernel_sha256'] == extra['kernel_sha256']
            and kernel.stat().st_size == lock['kernel_bytes'] == built['kernel_bytes'] == layout['kernel_bytes'],
            'actual diagnostic kernel bytes match every independent freeze file')
    actual = prior.MachO(kernel.read_bytes())
    require(actual.base == 0xfffffff007004000 and actual.end - actual.base == layout['loaded_span_bytes']
            == lock['loaded_span_bytes'] == built['loaded_span_bytes']
            and actual.loaded_hash == layout['loaded_span_sha256'] == lock['loaded_span_sha256'] == built['loaded_span_sha256'],
            'independent diagnostic Mach-O reconstruction agrees with all loaded-span metadata')
    symbols = {'entry_offset': '__start', 'vectors_offset': '_LowExceptionVectorBase',
        'entry_sync_offset': '_LowResetVectorBase', 'bootstrap_pagetables_offset': '_bootstrap_pagetables',
        'intstack_offset': '_intstack', 'intstack_top_offset': '_intstack_top',
        'excepstack_offset': '_excepstack', 'excepstack_top_offset': '_excepstack_top'}
    for field, symbol in symbols.items():
        require(layout[field] == actual.symbols[symbol] - actual.base, 'actual diagnostic symbol-derived geometry: ' + field)
    require(layout['entry_sync_bytes'] == align(actual.symbols['_thor_entry09_asm_end']) - actual.symbols['_LowResetVectorBase'],
            'actual diagnostic reset/vector instruction synchronization extent')
    head = subprocess.check_output(['git', '-C', str(xsrc), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == lock['xnu_commit'] == built['final_HEAD'] == extra['source_commit']
            and not subprocess.check_output(['git', '-C', str(xsrc), 'status', '--porcelain'], text=True).strip(),
            'exact clean MEM08 audited XNU source revision')
    require(lock['build_source_commit'] == built['actual_build_source_commit']
            and lock['compiled_source_content_equivalent_to_final_HEAD'] is True
            and built['compiled_source_content_equivalent_to_final_HEAD'] is True,
            'actual compilation revision is distinguished from later audit-only changes')
    delta = subprocess.check_output(['git', '-C', str(xsrc), 'diff', '--name-only', lock['build_source_commit'], head], text=True).splitlines()
    allowed = {'scripts/audit-thor-mem08-linked.py', 'tests/test_thor_mem08_log.py', 'tests/test_thor_mem08_render.py'}
    require(delta == lock['post_build_changes'] == built['post_build_changes'] and set(delta) <= allowed,
            'only declared auditor and host tests may differ after final real compilation')
    require(sha(candidate / 'kernel-layout.json') == built['layout_sha256']
            and sha(xproof) == sha(candidate / 'machinecode.json') == lock['mem08_machinecheck_sha256']
            == lock['machinecheck_sha256'] == built['machinecode_sha256'] == built['coverage_machinecode_sha256'],
            'exact independent geometry and native-machinecode proof file hashes')
    require(lock['frontier'] == built['frontier'] == extra['frontier'] == 'native-arm-init-first-kprintf-return'
            and lock['MEM08_compiled_stage_prefix'] == list(range(16)) and lock['MEM08_reserved_slots'] == [],
            'MEM08 frontier is first real native log return with sixteen active slots')
    repeat = candidate / 'mem08-reproducible-link.json'; doc = json.loads(repeat.read_text())
    require(sha(repeat) == built['reproducible_link_sha256'] and doc['result'] == 'PASS_TWO_FORCED_LINKS_BYTE_IDENTICAL'
            and doc['kernel_sha256'] == digest, 'two genuine forced links produced identical new diagnostic kernel bytes')
    for relative in doc['link_receipts']:
        item = json.loads(rooted(root, relative).read_text())
        require(item['phase'] == 'kernel-linked-and-sealed' and item['kernel']['sha256'] == digest
                and all(c['returncode'] == 0 for c in item['commands']), 'successful native link receipt: ' + relative)
    regression = check_regression_receipts(root, xsrc, built, require)
    require(extra['hardware_tested'] is False and extra['phone_actions'] == 0
            and extra['full_VM_initialization_proven'] is False and extra['normal_machine_init_reached'] is False,
            'new native kernel evidence does not claim hardware, full VM, platform or iOS success')
    return dict(kernel_sha256=digest, build_source_commit=lock['build_source_commit'], audited_source_commit=head,
                regressions=regression), actual, extra, built, lock, layout


def validate_mem08_startup_binary(actual, extra, require):
    p = prior_module(); startup = extra['native_startup']
    address, length, offset, raw = p.startup_section(actual)
    index, counts = p.startup_table_contract(raw, actual.symbols['_PE_init_kprintf'])
    table = startup['dispatch_table']
    require(table['section_address'] == hex(address) and table['section_file_offset'] == offset
            and table['bytes'] == length and table['entry_size'] == 24 and table['entries'] == length // 24
            and table['table_sha256'] == sha_bytes(raw), 'independent actual native startup section and all 24-byte records')
    expected = dict(record_address=hex(address + index * 24), file_offset=offset + index * 24,
                    subsystem=4, rank=0, func=hex(actual.symbols['_PE_init_kprintf']), arg=0)
    require(table['unique_KPRINTF_record'] == expected and table['subsystems_at_or_below_KPRINTF'] == counts,
            'the sole actual KPRINTF initializer is bound to PE_init_kprintf')
    pc = actual.symbols['_thor_mem07_kprintf_callback']
    adrp, add, ret = struct.unpack('<III', p.data_at_va(actual, pc, 12))
    require(adrp & 0x9f00001f == 0x90000000 and add & 0xffc003ff == 0x91000000 and ret == 0xd65f03c0,
            'retained native callback getter contains exactly ADRP ADD RET')
    immediate = ((adrp >> 5) & 0x7ffff) << 2 | ((adrp >> 29) & 3)
    if immediate & (1 << 20): immediate -= 1 << 21
    callback = (pc & ~4095) + (immediate << 12) + ((add >> 10) & 4095)
    require(callback == actual.symbols['_PE_init_kprintf'] == int(startup['callback_getter']['decoded_callback'], 0),
            'actual callback getter decodes the table-bound native address')
    sites = startup['call_sites']; require(len(sites) > 30 and len({s['address'] for s in sites}) == len(sites),
            'MEM08 native call/branch audit has complete unique sites')
    for site in sites:
        pc = int(site['address'], 0)
        require(p.branch_target(actual, pc, site['branch_kind']) == actual.symbols[site['target']]
                == int(site['target_address'], 0) and actual.contains_function(site['containing_function'], pc),
                'raw actual native ' + site['branch_kind'] + ' target: ' + site['target'])
    order = startup['natural_arm_init_order']
    required = ['_arm_vm_init', '_thor_mem07_after_vm', '_PE_consistent_debug_inherit', '_thor_mem07_debug_returned',
        '_serial_init', '_thor_mem07_serial_returned', '_kernel_startup_initialize_upto', '_thor_mem07_kprintf_returned',
        '_kprintf', '_thor_mem08_kprintf_done', '_thor_mem08_serial_configured']
    require([s['target'] for s in order] == required and [int(s['address'], 0) for s in order]
            == sorted(int(s['address'], 0) for s in order), 'natural arm_init executes first real kprintf before bounded serial-configured stop')
    for site in order:
        pc = int(site['address'], 0)
        require(p.branch_target(actual, pc, 'BL') == actual.symbols[site['target']] and actual.contains_function('_arm_init', pc),
                'actual arm_init mandatory direct BL: ' + site['target'])
    require(extra['native_caller']['natural_order'] == order and extra['native_caller']['native_order_and_guard_dominance'] is True
            and extra['native_caller']['no_unchecked_caller_return'] is True,
            'full native CFG requires complete order and prevents caller continuation past stop')
    require(not any(s['containing_function'] == '_arm_init' and int(s['address'], 0) > int(order[0]['address'], 0) and s['target'] in
                {'_PE_create_console', '_PE_init_platform', '_kernel_bootstrap', '_machine_init'} for s in sites),
            'new native arm_init cannot reach console, later platform or kernel bootstrap')
    serial = startup['serial_no_mmio']; branch = int(serial['zero_SoC_branch'], 0); word = actual.word(branch)
    require(word & 0xff00001f == 0xb4000000, 'native serial zero-SoC path uses actual CBZ x0')
    delta = (word >> 5) & 0x7ffff
    if delta & (1 << 18): delta -= 1 << 19
    path = [int(v, 0) for v in serial['no_SoC_path']]
    require(branch + delta * 4 == path[0] and len(path) < 32 and actual.word(path[-1]) == 0xd65f03c0,
            'bounded native serial path reaches the actual zero-device return')
    for pc in path:
        word = actual.word(pc)
        require(word & 0xfc000000 != 0x94000000 and word & 0xfffffc1f not in {0xd61f0000, 0xd63f0000},
                'guarded zero-SoC serial path cannot invoke a driver or indirect MMIO callback')
    for key in ('consistent_debug_unmapped', 'no_early_pmap_map_globals', 'slot2_empty_all_checkpoints'):
        require(startup[key] is True, 'retained natural startup isolation: ' + key)
    reset = startup['reset']
    require(reset['PASS_hold_seconds'] == 12 and reset['RESET_NEXT_hold_seconds'] == 1
            and reset['iteration_bound'] == 0x40000000 and reset['stalled_counter_bound'] == 4096
            and reset['native_counter_hz'] == 19200000 and reset['all_wait_callers_are_constant_bounded'] is True,
            'native final log PASS and reset marker have bounded counter/stall/iteration waits')
    view_labels = p.macho_symbol_bytes(actual, '_thor_mem07_view_labels', 160)
    view_states = p.macho_symbol_bytes(actual, '_thor_mem07_view_states', 80)
    wanted_labels = ['BOOT LOG', 'VM RETURN', 'SERIAL CALL', 'SERIAL RETURN', 'KPRINTF CALL',
                     'NAT KPRINTF', 'RESET NEXT', 'BOOT LOG', 'BOOT LOG', 'BOOT LOG']
    wanted_states = ['WAIT', 'PASS', 'CALL', 'RETURN', 'CALL', 'PASS', 'PASS', 'CALL', 'RETURN', 'PASS']
    require([view_labels[i*16:(i+1)*16].split(b'\0')[0].decode() for i in range(10)] == wanted_labels
            and [view_states[i*8:(i+1)*8].split(b'\0')[0].decode() for i in range(10)] == wanted_states,
            'actual native view tables retain all ten log/wait/call/return/final states')
    for key, argument in [('PASS_call', 9), ('RESET_NEXT_call', 6)]:
        pc = int(reset[key], 0)
        require(p.branch_target(actual, pc, 'BL') == actual.symbols['_thor_mem07_view']
                and actual.contains_function('_thor_mem08_serial_configured', pc)
                and actual.movz(pc-4, 0) == argument,
                'raw final serial-configured view call has independently decoded constant: ' + key)
    pc = int(reset['reset_call'], 0)
    require(p.branch_target(actual, pc, 'BL') == actual.symbols['_thor_entry09_reset']
            and actual.contains_function('_thor_mem08_serial_configured', pc)
            and int(reset['PASS_call'], 0) < int(reset['RESET_NEXT_call'], 0) < pc,
            'actual final PASS then RESET_NEXT then reset calls occur only in bounded native stop')
    return {'actual_startup_records': length // 24, 'natural_KPRINTF_callback': hex(callback),
            'first_natural_kprintf_compiled': True, 'stops_before_console': True, 'platform_or_iOS_proven': False}


def validate_native_log_guards(root, output, xsrc, candidate, extra, built, lock, require, replay):
    path = candidate / 'mem08-native-log-guards.json'; document = json.loads(path.read_text())
    require(sha(path) == built['native_log_guards_sha256'] == lock['native_log_guards_sha256'],
            'native frozen candidate binds exact production C observer guard proof')
    require(document['schema'] == 'thor-mem08-production-C-log-guards-v1'
            and document['result'] == 'PASS_NATIVE_LOG_GUARDS_WITH_FAULTS' and document['phone_actions'] == 0
            and document['source_root'] == str(xsrc), 'native C guard proof has exact new-source and host-only scope')
    sources = document['source_sha256']
    require(set(LOG_BASELINE_TUS) <= set(sources), 'C guard proof covers all six actual observed native TUs')
    for relative, digest in sources.items():
        pin(rooted(xsrc / '.cache/xnu-work', relative), digest, 'actual production guard TU/header: ' + relative)
        require(extra['prepared_source_sha256'][relative] == digest, 'linked source inventory covers executed guard input: ' + relative)
    counters = sorted(set(name for relative in ('libkern/os/log.c', 'libkern/os/log_queue.c', 'bsd/kern/subr_log_stream.c')
        for name in re.findall(r'(?m)^SCALABLE_COUNTER_DEFINE\(\s*(\w+)\s*\)',
                               (xsrc / '.cache/xnu-work' / relative).read_text())))
    require(len(counters) == 31 and counters == document['counter_fixture_symbols'],
            'all thirty-one declared host counter fixtures derive independently from actual native declarations')
    functions = document['production_function_sha256']
    require(document['production_function_count'] == len(functions) == 16
            and set(functions) == set(document['production_function_sources'])
            and {'thor_mem08_log_begin', 'thor_mem08_log_common', 'thor_mem08_log_dispatch_guard',
                 'thor_mem08_log_message_guard', 'thor_mem08_log_message_done', 'thor_mem08_log_dispatch_done',
                 'thor_mem08_log_finished', 'thor_mem08_log_confirm', 'thor_mem08_clock_state',
                 'thor_mem08_stream_state'} <= set(functions),
            'actual production observer function closure has sixteen functions including each dispatch/message/clock/stream boundary')
    cases = document['cases']
    require(document['tests_run'] == len(cases) == 122 and len({item['case'] for item in cases}) == 122
            and all(type(item['actual_exit']) is int and item['actual_exit'] == item['expected_exit'] for item in cases)
            and sum(item['actual_exit'] != 0 for item in cases) >= 90,
            'one hundred twenty-two unique actual guard executions include complete refusal controls')
    guarded = sorted(set(extra['native_log']['logging_counter_write_targets']
        + extra['native_log']['queue_counter_write_targets'] + extra['native_log']['stream_counter_write_targets']))
    require(len(guarded) == 27 and set(guarded) < set(counters) and document['guarded_counter_symbols'] == guarded
            and document['counter_fixture_count'] == 31 and document['guarded_counter_count'] == 27,
            'thirty-one declared fixtures remain distinct from twenty-seven actually reachable guarded counter targets')
    test = xsrc / 'tests/test_thor_mem08_log.py'; pin(test, document['test_source_sha256'], 'same actual production guard extraction/test source')
    require(len(document['substitutions']) == 7 and any('does not execute' in item for item in document['substitutions']),
            'host fixture substitutions explicitly preserve the independent native logger/CFG verification requirement')
    if replay:
        with tempfile.TemporaryDirectory(prefix='mem08-independent-log-guards-', dir=output.parent) as temp:
            fresh = Path(temp) / 'guards.json'; env = os.environ.copy()
            env.update(THOR_MEM08_SOURCE_ROOT=str(xsrc), THOR_MEM08_LOG_AUDIT=str(fresh))
            run = subprocess.run([sys.executable, '-B', str(test), '-q'], env=env, capture_output=True, text=True, timeout=120)
            require(run.returncode == 0 and 'Ran 122 tests' in run.stderr and fresh.is_file(),
                    'freshly compile and execute all actual production C log guard fault cases')
            require(same_document(json.loads(fresh.read_text()), document),
                    'fresh actual production guard extraction, harness and every fault result reproduce frozen proof exactly')
    return {'proof_sha256': sha(path), 'production_functions': 16, 'guard_cases': 122, 'counter_fixtures': 31,
            'reachable_guarded_counter_targets': 27,
            'host_guard_execution_only': True, 'native_log_algorithm_host_execution_claimed': False}



def validate_retained_vm_guards(actual, extra, xsrc, require):
    require(extra['retirement_boundary']['no_low_diagnostic_calls_in_retired_native_interval'] is True,
            'retired native interval cannot reach low framebuffer diagnostics')
    display, renderer = extra['display_geometry'], extra['renderer']
    require(display['minimum_active_text_contrast'] >= 18 and display['WAIT_text_contrast'] >= 18
            and display['runtime_slot_palette_and_sequential_mask_gates'] is True,
            'native tile text contrast and monotonic stage-state guards remain reviewed')
    require(all(renderer[key] is True for key in ('actual_glyph_pixel_stores',
        'text_before_tile_cache_clean_and_DSB', 'no_low_framebuffer_constants')),
        'actual native glyph stores stay in high alias and complete before cache maintenance')
    cpu = extra['exact_CPU_write_proof']; p = prior_module()
    require(cpu['current_CPUData_exact_VA_write_probe_before_CALL11'] is True
            and cpu['nested_member'] == 'cpu_data_t.cpu_pmap_cpu_data.cpu_number'
            and int(cpu['actual_original_CPU_VA'], 0) == actual.symbols[cpu['BootCpuData_linkage']] + 640,
            'exact original CPU object and four-byte nested member remain write-probed')
    require(actual.word(int(cpu['actual_writable_AT'], 0)) & 0xffffffe0 == 0xd5087820,
            'raw A64 writable AT instruction still probes original CPU write address')
    word = actual.word(int(cpu['actual_native_STRW'], 0))
    require(word & 0xffc00000 == 0xb9000000 and ((word >> 10) & 4095) * 4 == 640,
            'raw native CPU-number STRW retains exact four-byte field offset')
    require(extra['native_PTD_geometry']['actual_ptd_per_page'] == 51
            and extra['native_PTD_geometry']['PAGE_SHIFT_input'] == 12,
            'actual native PTD arithmetic remains fifty-one per 4KiB page')
    for key, phase in [('pmap_phase1', 1), ('pmap_phase2', 2)]:
        require(extra[key]['phase'] == phase and extra[key]['guard_dominates_dynamic_zero'] is True,
                'native metadata clear remains dominated by correct phase guard: ' + key)
    for target, address in extra['native_tail']['native_tail_calls'].items():
        pc = int(address, 0)
        require(p.branch_target(actual, pc, 'BL') == actual.symbols[target]
                and actual.contains_function('_arm_vm_init', pc), 'raw guarded native VM tail call: ' + target)
    for address in extra['native_tail']['native_return_sites']:
        pc = int(address, 0)
        require(actual.word(pc) == 0xd65f03c0 and actual.contains_function('_arm_vm_init', pc),
                'actual early native VM return instruction remains a bounded RET')
    for key in ('old_pmap_guards_exactly_preserved_except_proven_alias_reindex',
                'old_low_VA_ledger_retained', 'empty_PTDS_pool_growth_rejected'):
        require(extra['source_checks'][key] is True, 'inherited native memory policy: ' + key)
    config = extra['compile_configuration']
    objects = xsrc / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT'
    for name in ('pmap', 'pmap_data', 'lock_group'):
        pin(objects / (name + '.o.json'), config[name + '_compile_record_sha256'],
            'actual unchanged native compiler record: ' + name)
    pin(objects / 'pmap_data.o', config['DWARF_object_sha256'], 'actual compiler DWARF native ABI object')


def native_symbol_rows(actual, kernel, symbol):
    first = actual.symbols[symbol]
    after = min(value for value in actual.symbols.values() if value > first)
    result = subprocess.run(['xcrun', 'llvm-objdump', '--disassemble', '--no-show-raw-insn',
        '--start-address=' + hex(first), '--stop-address=' + hex(after), str(kernel)],
        capture_output=True, text=True, timeout=60)
    check(result.returncode == 0, 'actual numeric-address disassembly handles native symbol aliases')
    rows = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r'\s*([0-9a-fA-F]+):\s+(.+)', line)
        if match:
            address = int(match[1], 16)
            if first <= address < after: rows.append((address, match[2].strip()))
    check(rows and rows[0][0] == first, 'actual native numeric-address instruction range is nonempty: ' + symbol)
    return rows


def validate_native_log_binary_and_stack(root, actual, extra, xsrc, candidate, built, lock, require):
    p = prior_module(); log = extra['native_log']
    for key in ('native_first_kprintf_call_compiled', 'normal_native_log_algorithm_compiled',
                'phase4_LOCKS_complete_KPRINTF_callback_returned', 'original_empty_bootargs_retained',
                'runtime_guards_reject_replay_allocation_and_dynamic_queue',
                'original_native_logging_algorithms_preserved', 'stream_disabled_returns_before_session_and_allocation'):
        require(log[key] is True, 'actual native logger boundary: ' + key)
    require(log['native_msgbuf_required_suffix'] == 'kprintf initialized\n' and log['native_msgbuf_bytes'] == 20
            and log['native_message_counter_increment'] == 1 and log['runtime_stack_guard_bytes'] == 12288
            and log['console_creation_reached'] is False and log['iOS_userspace_started'] is False,
            'real first log requires exact message tail, counter advance and pre-console stack guard')
    for item in log['call_sites']:
        pc = int(item['address'], 0)
        require(p.branch_target(actual, pc, item['branch_kind']) == actual.symbols[item['target']]
                == int(item['target_address'], 0) and actual.contains_function(item['containing_function'], pc),
                'independent raw native logging branch: ' + item['target'])
    addresses = [int(v, 0) for v in log['original_VA_write_AT_and_sampled_PAR']]
    require(len(addresses) == 2 and len(set(addresses)) == 2, 'actual span guard has distinct peeled first-page and loop AT sites')
    for pc in addresses:
        require(actual.contains_function('_thor_mem08_log_span_guard', pc)
                and actual.word(pc) & 0xffffffe0 == 0xd5087820 and actual.word(pc + 4) == 0xd5033fdf
                and actual.word(pc + 8) & 0xffffffe0 == 0xd5387400,
                'raw original-VA writable AT is immediately followed by ISB and PAR_EL1 capture')
    path = candidate / 'mem08-native-log-stack.json'; document = json.loads(path.read_text())
    require(sha(path) == lock['native_log_stack_sha256'] == built['native_log_stack_sha256'],
            'candidate and genuine build freeze exact selected native-log stack receipt')
    require(document['schema'] == 'thor-mem08-native-log-stack-v1'
            and document['result'] == 'PASS_SELECTED_NATIVE_LOG_STACK_BOUND'
            and document['kernel_sha256'] == sha_bytes(actual.data) and document['kernel_bytes'] == len(actual.data)
            and document['source_commit'] in {lock['build_source_commit'], lock['xnu_commit']}
            and document['phone_actions'] == 0 and document['hardware_tested'] is False,
            'selected static stack analysis binds this actual kernel and reviewed equivalent source revision')
    producer = rooted(root, document['producer_path']); pin(producer, document['producer_sha256'], 'exact selected-path stack evidence producer')
    expected = set('log log_queue printf subr_log subr_log_stream rtclock arm_init arm_vm_init pe_kprintf pe_serial startup log_encode clock subr_prf lock_ticket'.split())
    require(set(document['actual_stack_usage']) == set(log['fresh_stack_usage']) == set(log['actual_compile_records']) == expected,
            'stack and compiler closure covers all fifteen actual native translation units')
    parsed = {}
    for name, item in document['actual_stack_usage'].items():
        su = rooted(xsrc, item['path']); pin(su, item['sha256'], 'actual native compiler stack output: ' + name)
        frames = {}
        for line in su.read_text().splitlines():
            site, amount, kind = line.split('\t'); check(kind == 'static', 'native first-log stack frame must have static bound')
            function = site.rsplit(':', 1)[-1]; check(function not in frames, 'native stack function appears once')
            frames[function] = int(amount)
        require(frames == item['functions'] == log['fresh_stack_usage'][name]['functions']
                and item['sha256'] == log['fresh_stack_usage'][name]['sha256'], 'independent parse of all native compiler frame sizes: ' + name)
        record = rooted(xsrc, item['compile_record']); pin(record, item['compile_record_sha256'], 'actual native compiler command record: ' + name)
        command = json.loads(record.read_text().strip().rstrip(',')); source = rooted(xsrc, item['source'])
        require('-fstack-usage' in command['arguments'] and Path(command['file']).resolve() == source
                and log['actual_compile_records'][name]['sha256'] == sha(record), 'real compile command selected audited source with stack output: ' + name)
        pin(source, item['source_sha256'], 'actual native compiled translation-unit bytes: ' + name)
        require(extra['prepared_source_sha256'][str(source.relative_to(xsrc / '.cache/xnu-work'))] == item['source_sha256'],
                'native source bytes agree with complete machinecode audit inventory: ' + name)
        pin(rooted(xsrc, item['object_path']), item['object_sha256'], 'actual native object used by selected first-log path: ' + name)
        parsed[name] = frames
    for relative, digest in document['source_sha256'].items():
        pin(rooted(xsrc, relative), digest, 'selected path source/constructor input: ' + relative)
    pin(rooted(xsrc, document['libdispatch_archive_path']), document['libdispatch_archive_sha256'], 'real linked private libdispatch archive')
    require(len(document['paths']) == 14, 'selected native returning paths have fourteen explicit reviewed chains')
    for item in document['paths']:
        require(item['frames'] and sum(frame['bytes'] for frame in item['frames']) == item['sum_bytes'],
                'independent selected native chain sum: ' + item['name'])
        for frame in item['frames']:
            if frame['basis'] == 'actual compiled .su':
                require(parsed[frame['translation_unit']][frame['symbol'][1:]] == frame['bytes'],
                        'selected chain frame matches actual compiler output: ' + frame['symbol'])
            else:
                require(document['raw_frame_evidence'][frame['symbol']]['frame_upper_bytes'] == frame['bytes'],
                        'external selected chain frame has raw executable evidence: ' + frame['symbol'])
    require(max(item['sum_bytes'] for item in document['paths']) == document['largest_listed_conservative_chain_bytes'] == 1888
            and document['conservative_bound_bytes'] == 2048 and document['runtime_guard_bytes'] == 12288,
            'selected first-log path bound is 2048 bytes with 10240 bytes runtime headroom')
    for symbol, item in document['raw_frame_evidence'].items():
        require(actual.symbols[symbol] == int(item['address'], 0), 'raw external frame belongs to actual symbol: ' + symbol)
        rows = native_symbol_rows(actual, candidate / 'kernel.macho', symbol)
        require(sha_bytes(b''.join(struct.pack('<I', actual.word(pc)) for pc, _ in rows)) == item['raw_instruction_sha256'],
                'independent fixed-helper disassembly and raw executable bytes reproduce whole external frame: ' + symbol)
        allocation = 0
        for _, instruction in rows:
            subtract = re.fullmatch(r'sub\s+sp,\s*sp,\s*#(0x[0-9a-f]+|[0-9]+)(?:,\s*lsl\s*#(12))?', instruction)
            if subtract: allocation += int(subtract[1], 0) << (12 if subtract[2] else 0)
            preindex = re.search(r'\[sp,\s*#-(0x[0-9a-f]+|[0-9]+)\]!', instruction)
            if preindex: allocation += int(preindex[1], 0)
            require(not re.match(r'\w+\s+sp,', instruction) or subtract is not None
                    or re.fullmatch(r'add\s+sp,\s*sp,\s*#(?:0x[0-9a-f]+|[0-9]+)', instruction)
                    or (re.fullmatch(r'mov\s+sp,\s*x29', instruction)
                        and any(re.fullmatch(r'mov\s+x29,\s*sp', value) for _, value in rows)),
                    'external selected frame has no unbounded or unknown SP adjustment: ' + symbol)
        require(allocation <= item['frame_upper_bytes'], 'independent raw SP-allocation upper bound: ' + symbol)
        for instruction in item['SP_instructions'] + item['direct_and_indirect_calls']:
            require(actual.word(int(instruction['address'], 0)) == int(instruction['word'], 0), 'actual executable SP/call word: ' + symbol)
    private = document['private_firehose']; pointer = actual.symbols[private['symbol']]
    require(pointer == int(private['address'], 0) and private['initial_zero_section']['zero_fill_type'] == 1,
            'private firehose pointer belongs to native zero-fill section')
    require(any(va + file_bytes <= pointer and pointer + 8 <= va + vm_bytes
                for va, vm_bytes, _, file_bytes, _ in actual.segments),
            'private firehose pointer is independently inside actual zero-fill loaded bytes')
    section = private['initial_zero_section']; require(int(section['address'], 0) <= pointer
        and pointer + 8 <= int(section['address'], 0) + section['size'], 'private pointer lies fully inside declared zero-fill extent')
    load = private['reserve_private_pointer_load']; adrp_pc, ldr_pc = int(load['ADRP_PC'], 0), int(load['LDR_PC'], 0)
    a, b = actual.word(adrp_pc), actual.word(ldr_pc); imm = ((a >> 5) & 0x7ffff) << 2 | ((a >> 29) & 3)
    if imm & (1 << 20): imm -= 1 << 21
    require(a & 0x9f000000 == 0x90000000 and b & 0xffc00000 == 0xf9400000
            and (b >> 5) & 31 == a & 31 and (adrp_pc & ~4095) + (imm << 12) + ((b >> 10) & 4095) * 8 == pointer,
            'independent raw ADRP/LDR resolves the actual private firehose BSS pointer')
    branch = private['reserve_NULL_branch']; pc = int(branch['address'], 0); word = actual.word(pc); delta = (word >> 5) & 0x7ffff
    if delta & (1 << 18): delta -= 1 << 19
    require(word & 0xff000000 == 0xb4000000 and word & 31 == b & 31
            and pc + delta * 4 == int(branch['target'], 0), 'actual NULL firehose pointer takes independently decoded CBZ')
    null_path = private['reserve_NULL_executed_path']
    require(int(null_path[0]['address'], 0) == pc + delta * 4 and actual.word(int(null_path[-1]['address'], 0)) == 0xd65f03c0,
            'actual NULL firehose path reaches return before allocation and dynamic callbacks')
    for instruction in null_path:
        word = actual.word(int(instruction['address'], 0))
        require(word == int(instruction['word'], 0) and word & 0xfc000000 != 0x94000000
                and word & 0xfffffc1f not in {0xd61f0000, 0xd63f0000}, 'actual NULL firehose path has no direct or indirect call')
    record = private['OSLOG_record']; va = int(record['address'], 0)
    require(struct.unpack('<IIQQ', p.data_at_va(actual, va, 24)) == (12, 1, actual.symbols[private['constructor_caller']], 0)
            and private['current_guarded_startup_phase'] == 4 and private['constructor_subsystem_not_reached'] is True,
            'actual firehose constructor startup record is phase twelve, beyond guarded phase four')
    pc = int(private['unique_constructor_BL'], 0)
    require(p.branch_target(actual, pc, 'BL') == actual.symbols[private['constructor_symbol']]
            and actual.contains_function(private['constructor_caller'], pc), 'raw constructor branch belongs only to later OSLOG initializer')
    cursor, callers = 32, []
    ncmds = struct.unpack_from('<I', actual.data, 16)[0]
    for _ in range(ncmds):
        command, length = struct.unpack_from('<II', actual.data, cursor)
        if command == 0x19:
            sections = struct.unpack_from('<I', actual.data, cursor + 64)[0]
            for index in range(sections):
                start = cursor + 72 + index * 80
                va, size, offset = struct.unpack_from('<QQI', actual.data, start + 32)
                flags = struct.unpack_from('<I', actual.data, start + 64)[0]
                if not flags & 0x80000400: continue
                require(size % 4 == 0 and offset + size <= len(actual.data), 'actual executable instruction section is bounded')
                for i, (word,) in enumerate(struct.iter_unpack('<I', actual.data[offset:offset + size])):
                    if word & 0xfc000000 != 0x94000000: continue
                    delta = word & 0x3ffffff
                    if delta & (1 << 25): delta -= 1 << 26
                    site = va + i * 4
                    if site + delta * 4 == actual.symbols[private['constructor_symbol']]: callers.append(site)
        cursor += length
    require(callers == [pc], 'independent scan of every executable section finds exactly the later phase-twelve constructor BL')
    return {'proof_sha256': sha(path), 'actual_native_stack_units': 15, 'selected_paths': 14,
            'conservative_static_stack_bytes': 2048, 'runtime_guard_bytes': 12288, 'runtime_stack_measurement_claimed': False}


def validate_mem08_xnu(root, output, xsrc, candidate, xproof, require, replay=True):
    frozen, actual, extra, built, lock, layout = validate_mem08_frozen_artifact(root, candidate, xsrc, xproof, require)
    kernel = candidate / 'kernel.macho'; p = prior_module()
    p.mem07_baseline_files(root, xsrc)
    prior = root / 'artifacts/candidates/thor-20261026-vmdev-016'
    pin(prior / 'machinecode.json', MEM07_MC, 'immutable MEM07 native baseline proof identity')
    previous = json.loads((prior / 'machinecode.json').read_text())
    baseline = xsrc / '.cache/mem08-baseline'
    for relative, digest in previous['prepared_source_sha256'].items():
        path = baseline / relative
        pin(path, digest, 'MEM08 old native prepared source from immutable MEM07 proof: ' + relative)
    reconstruction = reconstruct_mem07_log_baseline(root, xsrc)
    require(True, 'all six newly observed log/clock/stream TUs independently reconstructed from fixed Apple and MEM07 ordered patches')
    mapping = independent_native_mapping_replay(root, kernel, extra, require)
    geometry_contract(extra['display_geometry']['geometry'])
    require(True, 'all sixteen native tiles shift exactly one row without overlapping UEFI reservation')
    palette = list(struct.unpack('<16I', p.macho_symbol_bytes(actual, '_thor_mem04_palette', 64)))
    raw_labels = p.macho_symbol_bytes(actual, '_thor_mem04_labels', 256)
    raw_states = p.macho_symbol_bytes(actual, '_thor_mem04_states', 128)
    labels = [raw_labels[i * 16:i * 16 + 16].split(b'\0')[0].decode() for i in range(16)]
    states = [raw_states[i * 8:i * 8 + 8].split(b'\0')[0].decode() for i in range(16)]
    numbered_display_contract(palette, labels, states, list(p.macho_symbol_bytes(actual, '_thor_mem04_font', 252)))
    require(True, 'actual linked native palette, final BOOT LOG label, terminal states and font are exact')
    contract = extra['checkpoint_contract']
    require(contract['compiled_stage_prefix'] == list(range(16)) and contract['reserved_slots'] == []
            and contract['number_base'] == 16 and len(contract['slots']) == 16, 'MEM08 exact sixteen-stage numbered contract')
    for i, item in enumerate(contract['slots']):
        require((item['slot'], item['id'], item['label'], item['state'], item['color_hex'])
                == (i, '%02X' % i, labels[i], states[i], '#%06X' % palette[i]), 'raw native slot identity: %02X' % i)
    events = [e for group in extra['compiled_stage_sites'].values() for e in group]
    require(sorted(e['slot'] for e in events) == list(range(16)) and all(e['color'] == '#%06X' % palette[e['slot']] for e in events),
            'native monotonically completed stage calls are unique and use their own palette')
    budget = extra['pmap_metadata_budget']; config = extra['compile_configuration']
    normalized = dict(page_shift=budget['PAGE_SHIFT'], mem_size=64 << 20, pt_desc_size=budget['pt_desc_bytes'],
        ptd_info_size=budget['ptd_info_bytes'], pt_index_max=budget['PT_INDEX_MAX'], pp_attr_size=2, pv_pointer_size=8,
        pte_size=8, max_l2_cline=budget['MAX_L2_CLINE'], pmap_max_asids=2048, bitmap_word_size=8,
        io_attr_table_size=0, io_filter_table_size=0, xnu_monitor=not config['non_MONITOR'],
        has_guarded_io_filter=not config['no_guarded_IO_filter'], has_16bit_asid=bool(config['HAS_16BIT_ASID']))
    own = p.pmap_budget_contract(normalized)
    require(own['total_pages'] == 43 and own['total_bytes'] == 176128 == budget['total_bytes'] and own['ptd_per_page'] == 51,
            'independent diagnostic pmap budget remains forty-three pages and fifty-one PTDs per page')
    require(config['DWARF_pt_desc_bytes'] == 64 and config['DWARF_pp_attr_uint16'] is True
            and config['no_KASAN'] is True and config['MAX_ASIDS'] == 2048
            and config['CPU_number_store_offset'] == 640 and config['CPU_number_store_bytes'] == 4,
            'actual compiler/DWARF metadata still selects the reviewed native 4KiB ABI')
    require(extra['native_tail']['two_native_L3_loops'] is True
            and extra['native_tail']['each_registration_dominated_by_write_guard'] is True
            and extra['native_tail']['native_return_dominated_by_pmap_and_allocation_preflight'] is True
            and extra['native_PTD_pop']['guard_dominates_PTDS_and_growth_branch'] is True,
            'new logger cannot weaken native page-table allocation and return guard dominance')
    require(extra['native_globals']['not_called'] is True and extra['native_globals']['slot2_remains_empty'] is True,
            'artificial early global mapper remains removed and reserved slot two remains empty')
    validate_retained_vm_guards(actual, extra, xsrc, require)
    startup = validate_mem08_startup_binary(actual, extra, require)
    guards = validate_native_log_guards(root, output, xsrc, candidate, extra, built, lock, require, replay)
    log = validate_native_log_binary_and_stack(root, actual, extra, xsrc, candidate, built, lock, require)
    mutations = extra['guard_removal_mutations_rejected']
    mandatory = {'remove_thor_mem05_vm_enter', 'remove_thor_mem07_after_vm', 'remove_thor_mem05_ptd_guard',
        'remove_thor_mem07_debug_returned', 'remove_thor_mem07_serial_returned',
        'remove_kernel_startup_initialize_upto', 'remove_thor_mem07_kprintf_returned',
        'remove_thor_mem08_kprintf_done', 'remove_thor_mem08_serial_configured', 'remove_thor_mem08_stream_state'}
    require(mandatory <= set(mutations) and all(mutations.values()), 'old native guards and both new log completion guards reject removal')
    require(len(extra['bootstrap_mutations_rejected']) >= 9 and all(extra['bootstrap_mutations_rejected'].values()),
            'reviewed native bootstrap mutation controls still reject malformed table producers')
    require(len(extra['validated_noreturn_CFG_helpers']) >= 20 and
            all(h['no_reachable_RET_or_indirect_tail'] for h in extra['validated_noreturn_CFG_helpers']),
            'cold fatal helper control-flow graphs cannot return into unchecked native code')
    for relative, digest in extra['prepared_source_sha256'].items():
        pin(xsrc / '.cache/xnu-work' / relative, digest, 'same final native prepared source: ' + relative)
    auditor = xsrc / 'scripts/audit-thor-mem08-linked.py'; pin(auditor, extra['auditor_sha256'], 'exact final native auditor source')
    if replay:
        with tempfile.TemporaryDirectory(prefix='mem08-independent-native-', dir=output.parent) as temp:
            fresh = Path(temp) / 'fresh-machinecode.json'
            run = subprocess.run([sys.executable, '-B', str(auditor), '--kernel', str(kernel), '--out', str(fresh)],
                capture_output=True, text=True, timeout=240)
            require(run.returncode == 0 and fresh.is_file(), 'fresh full native PTE, logger, guard-mutation and CFG replay succeeds')
            require(same_document(json.loads(fresh.read_text()), extra), 'fresh full native replay exactly equals frozen proof semantics')
    return {'frozen_artifact': frozen, 'native_startup': startup, 'production_log_guards': guards, 'native_log_stack': log, 'independent_mapping_replay': mapping,
            'independent_log_source_reconstruction': reconstruction, 'independent_pmap_budget': own,
            'first_natural_kprintf_compiled': True, 'full_VM_or_iOS_proven': False}, extra


INPLACE_HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <CommonCrypto/CommonDigest.h>
#include "thor_mem08_full.h"
#include "BaseUefiDecompressLibInternals.h"
typedef struct {unsigned char *Window;unsigned Hashes,Infos,Decodes,Writes;} CTX;
static int allow(void *p){(void)p;return 1;}
static int hash(void *p,const void *data,M08_U64 n,const M08_U8 *want){
 unsigned char sum[32];((CTX*)p)->Hashes++;if(n>UINT32_MAX)return 0;
 CC_SHA256(data,(CC_LONG)n,sum);return memcmp(sum,want,32)==0;}
static int info(void *p,const void *data,M08_U32 n,M08_U32 *out,M08_U32 *scratch){
 ((CTX*)p)->Infos++;return UefiDecompressGetInfo(data,n,out,scratch)==0;}
static int decode(void *p,const void *data,void *out,void *scratch){
 ((CTX*)p)->Decodes++;return UefiDecompress(data,out,scratch)==0;}
static int writable(void *p,M08_U64 base,M08_U64 bytes){CTX *c=p;c->Writes++;
 return base==(M08_U64)(uintptr_t)c->Window&&bytes==M08_WINDOW_BYTES;}
static int intact(const unsigned char *p,size_t n){for(size_t i=0;i<n;i++)if(p[i]!=0xa5)return 0;return 1;}
static unsigned char *readfile(const char *name,size_t *n){FILE *f=fopen(name,"rb");if(!f)return NULL;
 if(fseek(f,0,SEEK_END))return NULL;long z=ftell(f);if(z<0||z>32*1024*1024)return NULL;rewind(f);
 unsigned char *p=malloc((size_t)z+1);if(!p||fread(p,1,(size_t)z,f)!=(size_t)z)return NULL;fclose(f);*n=(size_t)z;return p;}
int main(int argc,char **argv){
 if(argc!=5)return 2;size_t hn=0,cn=0;unsigned char *h=readfile(argv[1],&hn),*c=readfile(argv[2],&cn);
 if(!h||!c||hn!=128||cn<8||strlen(argv[4])!=64)return 3;
 M08_EXPECTED expected;memcpy(expected.Header,h,128);
 for(unsigned i=0;i<32;i++){unsigned v;if(sscanf(argv[4]+i*2,"%2x",&v)!=1)return 4;expected.LoadedSha256[i]=(unsigned char)v;}
 size_t sn=4096+128+cn+4096;unsigned char *sb=calloc(1,sn);unsigned char *wb=malloc((size_t)M08_WINDOW_BYTES+64);
 if(!sb||!wb)return 5;memcpy(sb+4096,h,128);memcpy(sb+4224,c,cn);memset(wb,0xa5,(size_t)M08_WINDOW_BYTES+64);
 CTX ctx={wb+32,0,0,0,0};M08_OPS ops={&ctx,allow,hash,info,decode,writable};
 M08_SOURCE source={sb,sn,(M08_U64)(uintptr_t)sb,1,1};M08_PACKET packet;M08_LOCATE_REPORT locate;
 static M08_WORK work;M08_REPORT result;unsigned char before[32],after[32];CC_SHA256(sb,(CC_LONG)sn,before);
 if(ThorMem08Locate(&source,&expected,&ops,&packet,&locate)!=M08_PASS||locate.Matches!=1||locate.HeaderOffset!=4096)return 6;
 if(ThorMem08Stage(&packet,&expected,&ops,ctx.Window,M08_WINDOW_BYTES,&work,&result)!=M08_PASS)return 7;
 CC_SHA256(sb,(CC_LONG)sn,after);
 if(memcmp(before,after,32)||!intact(wb,32)||!intact(wb+32+M08_WINDOW_BYTES,32)||work.Busy)return 8;
 if(result.Status||result.Phase!=5||result.SegmentRecords!=18||result.NonzeroSegments!=15||result.LoadedSpan!=M08_LOADED_SPAN||
    result.CodecOutputBytes!=M08_RAW_BYTES||result.CodecScratchBytes!=13376||result.ScratchOffset!=M08_MIN_USED||
    result.SourceStable!=1||result.NeverEntered!=1||result.DecodeCalls!=1||result.HashCalls!=4||
    result.CopiedBytes+result.ZeroedBytes!=M08_LOADED_SPAN||ctx.Hashes!=5||ctx.Infos!=1||ctx.Decodes!=1||ctx.Writes!=1)return 9;
 FILE *f=fopen(argv[3],"wb");if(!f||fwrite(ctx.Window,1,(size_t)M08_LOADED_SPAN,f)!=(size_t)M08_LOADED_SPAN||fclose(f))return 10;
 printf("{\"segment_record_count\":%u,\"nonzero_segment_count\":%u,\"loaded_span_bytes\":%llu,\"copied_bytes\":%llu,\"zeroed_bytes\":%llu,\"decode_calls\":%u,\"real_hash_calls\":%u,\"source_stable\":true,\"window_canaries_intact\":true,\"full_kernel_entry_executed\":false}\n",
 result.SegmentRecords,result.NonzeroSegments,(unsigned long long)result.LoadedSpan,(unsigned long long)result.CopiedBytes,(unsigned long long)result.ZeroedBytes,result.DecodeCalls,ctx.Hashes);
 free(h);free(c);free(sb);free(wb);return 0;
}
'''


def production_inplace_replay(root, src, asset, header, manifest, require, temp_parent):
    directory = src / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe'
    if not (directory / 'thor_mem08_full.c').is_file():
        directory = src
    check((directory / 'thor_mem08_full.c').is_file() and (directory / 'thor_mem08_full.h').is_file(),
          'explicit source contains the real MEM08 production stager')
    codec = root / 'evidence/build/vm-mem08/host-codec/source'
    paths = [directory / 'thor_mem08_full.c', directory / 'thor_mem08_full.h']
    with tempfile.TemporaryDirectory(prefix='mem08-independent-inplace-', dir=temp_parent) as temporary:
        temporary = Path(temporary); harness = temporary / 'inplace.c'; exe = temporary / 'inplace'
        output = temporary / 'expanded-span.bin'; harness.write_text(INPLACE_HARNESS)
        command = ['xcrun', 'clang', '-std=c11', '-O2', '-DTHOR_MEM08_HOST', '-Wno-deprecated-declarations',
            '-I', str(directory), '-I', str(codec), str(harness), str(directory / 'thor_mem08_full.c'),
            str(codec / 'BaseUefiDecompressLib.c'), '-o', str(exe)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        require(result.returncode == 0 and exe.is_file(), 'freshly compile independent real-SHA production in-place loader harness: ' + result.stderr[-1600:])
        result = subprocess.run([str(exe), str(header), str(asset), str(output), FULL_SPAN_SHA],
                                capture_output=True, text=True, timeout=120)
        require(result.returncode == 0 and output.is_file(),
                'fresh production locator, EFI decoder and reverse-expansion C execute the actual complete APFS asset: rc=' + str(result.returncode) + ' ' + result.stderr[-1600:])
        summary = json.loads(result.stdout)
        require(output.stat().st_size == FULL_SPAN and sha(output) == FULL_SPAN_SHA,
                'independent file hash confirms every copied, zero-gap and BSS byte in complete expanded span')
        require(summary['segment_record_count'] == 18 and summary['nonzero_segment_count'] == 15
                and summary['decode_calls'] == 1 and summary['real_hash_calls'] == 5
                and summary['source_stable'] is True and summary['window_canaries_intact'] is True
                and summary['full_kernel_entry_executed'] is False,
                'actual production stage has correct record counts, true SHA checks, stable source and intact output guards')
        summary.update(loaded_span_sha256=sha(output), raw_source_sha256=RAW_SHA,
            harness_sha256=sha_bytes(INPLACE_HARNESS.encode()), harness_origin='embedded in exact verifier source',
            source_files_sha256={str(p): sha(p) for p in paths})
    return summary, {str(p): p for p in paths}


def validate_full_loader(root, src, asset, manifest_path, require, temp_parent):
    manifest, fields, plan, files = full_loader_inputs(root, asset, manifest_path, require)
    codec, codec_files = production_codec_replay(root, asset, manifest, require, temp_parent)
    header = rooted(root, manifest['header_path'])
    inplace, inplace_files = production_inplace_replay(root, src, asset, header, manifest, require, temp_parent)
    files.update(codec_files); files.update(inplace_files)
    faults, fault_files = production_full_loader_fault_replay(root, src, require, temp_parent)
    files.update(fault_files)
    production_sha, sha_files = production_sha_replay(root, src, require, temp_parent)
    files.update(sha_files)
    shared_path = Path(__file__).with_name('full_loader_asset.py')
    shared = load_module('mem08_canonical_asset_metadata', shared_path)
    canonical = shared.validate(root, asset, manifest_path)
    require(canonical['raw_sha256'] == RAW_SHA and canonical['loaded_span_sha256'] == FULL_SPAN_SHA
            and canonical['compressed_sha256'] == fields['compressed_sha256']
            and canonical['header_sha256'] == fields['header_sha256'] and canonical['full_kernel_entry_executed'] is False,
            'canonical controller metadata agrees with independent raw parsing and both actual production C executions')
    files[str(shared_path)] = shared_path
    return {'inputs': canonical, 'production_codec_replay': codec, 'production_inplace_replay': inplace,
            'production_fault_replay': faults, 'production_sha_replay': production_sha}, files


def production_sha_replay(root, src, require, temp_parent):
    runner = root / 'evidence/build/vm-mem08/independent-production-sha/replay_production_sha.py'
    require(runner.is_file(), 'independent actual Mem08Hash and project SHA execution runner exists')
    with tempfile.TemporaryDirectory(prefix='mem08-independent-production-sha-', dir=temp_parent) as temp:
        output = Path(temp) / 'production-sha.json'
        run = subprocess.run([sys.executable, '-B', str(runner), '--root', str(root), '--source', str(src), '--out', str(output)],
            capture_output=True, text=True, timeout=180)
        require(run.returncode == 0 and output.is_file(), 'fresh compile and execution of actual Mem08Hash and production SHA for full 113MB span: ' + run.stderr[-1000:])
        document = json.loads(output.read_text())
        require(document['schema'] == 'thor-mem08-independent-production-sha-host-v1'
                and document['result'] == 'PASS_REAL_PRODUCTION_SHA_FULL_LOADED_SPAN'
                and document['source_root'] == str(src) and document['tests_run'] == len(document['results']) == 8
                and document['phone_actions'] == 0 and document['hardware_tested'] is False,
                'actual production SHA replay has complete eight-case host-only provenance')
        require(document['raw_bytes'] == RAW_BYTES and document['raw_sha256'] == RAW_SHA
                and document['loaded_span_bytes'] == FULL_SPAN and document['loaded_span_sha256'] == FULL_SPAN_SHA
                and document['actual_production_Mem08Hash'] is True and document['actual_production_ThorPayloadSha256'] is True
                and document['actual_project_OpenSSL_SHA_C'] is True,
                'actual adapter and original project SHA algorithm hash every byte beyond the old 32MiB parser boundary')
        files = {str(runner): runner}
        for name, digest in document['source_files_sha256'].items():
            path = rooted(root, name); pin(path, digest, 'actual production SHA C/header/extraction input: ' + path.name)
            files[str(path)] = path
        require(str(runner) in files and document['host_substitutions'], 'actual SHA replay declares fixtures and pins its own runner')
        return document, files


def inventory(files):
    return {'frozen_input_sha256': {name: sha(path) for name, path in sorted(files.items())},
            'frozen_input_bytes': {name: path.stat().st_size for name, path in sorted(files.items())}}


def cached_full_loader_precheck(root, path, expected, src, asset, manifest_path, require):
    pin(path, expected, 'explicit complete MEM08 full-loader precheck SHA256')
    document = json.loads(path.read_text())
    require(document['schema'] == 'thor-mem08-public-full-loader-precheck-v1'
            and document['result'] == 'PASS_PUBLIC_FULL_LOADER_PRECHECK'
            and document['verifier_sha256'] == sha(Path(__file__)) and document['phone_actions'] == 0
            and document['seed_generated'] is False and document['full_L2_ready'] is False,
            'cached full-loader precheck comes from exact verifier with no seed, boot or full-L2 claim')
    require(document['conditions_count'] == len(document['checks']) and all(v['passed'] for v in document['checks']),
            'completed preseed production codec/layout checks all passed')
    for name, digest in document['frozen_input_sha256'].items():
        item = rooted(root, name); pin(item, digest, 'unchanged full-loader precheck frozen input: ' + item.name)
        require(item.stat().st_size == document['frozen_input_bytes'][name], 'unchanged full-loader precheck input byte length')
    manifest, fields, plan, files = full_loader_inputs(root, asset, manifest_path, require)
    require(document['production_codec_replay']['decoded_raw_sha256'] == RAW_SHA
            and document['production_inplace_replay']['loaded_span_sha256'] == FULL_SPAN_SHA
            and document['production_sha_replay']['loaded_span_sha256'] == FULL_SPAN_SHA
            and document['production_sha_replay']['actual_production_Mem08Hash'] is True,
            'cached actual decoder and in-place execution prove exact raw and loaded bytes')
    directory = src / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe'
    for filename in ('thor_mem08_full.c', 'thor_mem08_full.h'):
        matching = [digest for name, digest in document['production_inplace_replay']['source_files_sha256'].items()
                    if Path(name).name == filename]
        require(len(matching) == 1 and sha(directory / filename) == matching[0],
                'actual final firmware production stager matches preseed executed source: ' + filename)
    canonical = load_module('mem08_asset_cached_metadata', Path(__file__).with_name('full_loader_asset.py')).validate(root, asset, manifest_path)
    require(document['inputs']['full_loader'] == canonical, 'cached full-loader metadata exactly matches final canonical input')
    return document


def production_full_loader_fault_replay(root, src, require, temp_parent):
    test = src / 'Platforms/Xiaomi/thorPkg/Tests/test_mem08_full_loader.py'
    directory = src / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe'
    if not test.is_file():
        test, directory = src / 'test_mem08_full_loader.py', src
    check(test.is_file(), 'explicit production full-loader fault suite is present')
    with tempfile.TemporaryDirectory(prefix='mem08-independent-full-faults-', dir=temp_parent) as temp:
        report = Path(temp) / 'full-loader-host-execution.json'; env = os.environ.copy()
        env.update(THOR_MEM08_PROJECT_ROOT=str(root), THOR_MEM08_FULL_SOURCE=str(directory),
                   THOR_MEM08_FULL_LOADER_AUDIT=str(report), PATH='/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin')
        run = subprocess.run([sys.executable, '-B', str(test), '-q'], capture_output=True, text=True, env=env, timeout=180)
        require(run.returncode == 0 and 'Ran 22 tests' in run.stderr and report.is_file(),
                'fresh UBSan production decoder/locator/inplace suite executes all twenty-two fault cases')
        doc = json.loads(report.read_text())
        require(doc['schema'] == 'thor-mem08-production-full-loader-host-execution-v1'
                and doc['phone_actions'] == 0 and doc['actual_production_decoder'] is True,
                'full-loader fault proof uses real production C and codec without a phone')
        expected = {'success': (0, 0), 'duplicate': (5, None), 'truncated': (6, None), 'bad_header': (6, None),
            'corrupt_source': (7, None), 'source_dead': (1, None), 'generation_locate': (3, None),
            'generation_stage': (0, 3), 'info_fail': (0, 8), 'info_raw': (0, 8), 'info_scratch': (0, 8),
            'non_writable': (0, 9), 'decode_fail': (0, 10), 'bad_raw_hash': (0, 11), 'bad_loaded_hash': (0, 14),
            'deadline_decode': (0, 2), 'generation_decode': (0, 3), 'deadline_copy': (0, 2),
            'deadline_zero': (0, 2), 'reentry': (0, 15), 'window_source_alias': (0, 1), 'report_window_alias': (0, 1)}
        require(len(doc['results']) == len(expected) and {r['mode'] for r in doc['results']} == set(expected),
                'production fault suite covers the complete explicit success/refusal matrix')
        for result in doc['results']:
            require((result['locate_status'], result['stage_status']) == expected[result['mode']]
                    and result['guard_pages'] is True and result['never_full_kernel_entered'] is True,
                    'production refusal occurs at the right boundary: ' + result['mode'])
            if result['mode'] in {'generation_stage', 'info_fail', 'info_raw', 'info_scratch',
                                   'non_writable', 'reentry', 'window_source_alias', 'report_window_alias'}:
                require(result['decode_calls'] == 0, 'unsafe preflight cannot reach decoder: ' + result['mode'])
        success = next(r for r in doc['results'] if r['mode'] == 'success')
        require(success['raw_sha256'] == RAW_SHA and success['loaded_span_sha256'] == FULL_SPAN_SHA,
                'independent fault harness also rehashes the actual original and fully expanded successful image')
        files = {}
        for name, digest in doc['source_files_sha256'].items():
            path = rooted(root, name); pin(path, digest, 'actual fault-executed production source: ' + path.name)
            files[str(path)] = path
        require(str(test) in files and str(directory / 'thor_mem08_full.c') in files,
                'fault proof covers selected exact production stager and its test')
        return dict(test_count=22, source_files_sha256=doc['source_files_sha256'],
                    header_sha256=doc['header_sha256'], compressed_sha256=doc['compressed_sha256'],
                    results=doc['results']), files


def collect_xnu_inputs(root, candidate, xsrc, xproof, result, extra):
    files = {str(p): p for p in candidate.glob('*.json')}
    for p in (candidate / 'kernel.macho', xproof, Path(__file__).resolve(),
              Path(__file__).with_name('verify_mem07_candidate.py')):
        files[str(p)] = p
    for name in extra['prepared_source_sha256']:
        p = xsrc / '.cache/xnu-work' / name; files[str(p)] = p
    for p in (xsrc / 'scripts').glob('audit-thor-*.py'):
        files[str(p)] = p
    for name in ('test_thor_mem08_log.py', 'test_thor_mem08_render.py'):
        p = xsrc / 'tests' / name; files[str(p)] = p
    files.update(prior_module().mem07_baseline_files(root, xsrc))
    for p in (xsrc / '.cache/mem08-baseline').rglob('*'):
        if p.is_file(): files[str(p)] = p
    for name in result['independent_log_source_reconstruction']['input_file_sha256']:
        files[name] = rooted(root, name)
    for name in result['independent_mapping_replay']['fixed_helper_input_sha256']:
        files[name] = rooted(root, name)
    stack = json.loads((candidate / 'mem08-native-log-stack.json').read_text())
    files[str(rooted(root, stack['producer_path']))] = rooted(root, stack['producer_path'])
    for item in stack['actual_stack_usage'].values():
        for field in ('path', 'compile_record', 'source', 'object_path'):
            p = rooted(xsrc, item[field]); files[str(p)] = p
    for relative in stack['source_sha256']:
        p = rooted(xsrc, relative); files[str(p)] = p
    p = rooted(xsrc, stack['libdispatch_archive_path']); files[str(p)] = p
    objects = xsrc / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    for p in objects.rglob('*.o.json'):
        files[str(p)] = p
    p = objects / 'osfmk/DEVELOPMENT/pmap_data.o'
    check(p.is_file(), 'actual native DWARF object exists'); files[str(p)] = p
    return files


def public_precheck_main(kind):
    parser = argparse.ArgumentParser(description='MEM08 public offline precheck; never generates seed or calls device tools')
    parser.add_argument('--' + kind, action='store_true', required=True)
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--out', type=Path, required=True)
    if kind == 'precheck-xnu':
        for name in ('candidate', 'xnu-source', 'xnu-proof'):
            parser.add_argument('--' + name, type=Path, required=True)
    else:
        for name in ('source', 'full-loader-asset', 'full-loader-manifest'):
            parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(); root = args.root.resolve(); output = rooted(root, args.out)
    check(output.is_relative_to(root / 'runs') and not output.exists(), 'new immutable public precheck output path')
    output.parent.mkdir(parents=True, exist_ok=True); require = Requirements()
    schema = 'thor-mem08-public-' + ('xnu' if kind == 'precheck-xnu' else 'full-loader') + '-precheck-v1'
    doc = dict(schema=schema, created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               verifier_sha256=sha(Path(__file__)), phone_actions=0, seed_generated=False, full_L2_ready=False)
    try:
        if kind == 'precheck-xnu':
            candidate, xsrc, xproof = (rooted(root, p) for p in (args.candidate, args.xnu_source, args.xnu_proof))
            result, extra = validate_mem08_xnu(root, output, xsrc, candidate, xproof, require)
            doc['compiled_kernel_frontier'] = result
            files = collect_xnu_inputs(root, candidate, xsrc, xproof, result, extra)
            doc['result'] = 'PASS_PUBLIC_XNU_PRECHECK'
        else:
            src, asset, manifest = (rooted(root, p) for p in (args.source, args.full_loader_asset, args.full_loader_manifest))
            result, files = validate_full_loader(root, src, asset, manifest, require, output.parent)
            doc.update(inputs={'full_loader': result['inputs']}, production_codec_replay=result['production_codec_replay'],
                production_inplace_replay=result['production_inplace_replay'], production_fault_replay=result['production_fault_replay'],
                production_sha_replay=result['production_sha_replay'])
            doc['result'] = 'PASS_PUBLIC_FULL_LOADER_PRECHECK'
        doc.update(inventory(files))
    except Exception as error:
        doc.update(result='FAIL', error_type=type(error).__name__, error=str(error))
    doc.update(checks=require.marks, conditions_count=len(require.marks))
    with output.open('x') as stream:
        json.dump(doc, stream, indent=2); stream.write('\n')
    print(json.dumps(dict(result=doc['result'], conditions_count=len(require.marks), out=str(output), phone_actions=0)))
    return 0 if doc['result'].startswith('PASS_') else 1


def executed_draw_events_contract(events):
    check(events, 'actual production draw trace is nonempty')
    frames = {}
    for item in events:
        check(type(item['frame']) is int and item['frame'] > 0, 'positive actual draw frame ID')
        frames.setdefault(item['frame'], []).append(item)
    check(sorted(frames) == list(range(1, len(frames) + 1)), 'complete consecutive production frame IDs')
    names = set()
    for items in frames.values():
        check(items[0]['kind'] == 'begin' and items[-1]['kind'] == 'pixels', 'production frame has explicit begin and pixel completion')
        name = items[0]['name']; names.add(name)
        check(items[1]['kind'] == 'clear' and items[1]['rect'] == [0, 0, 1440, 3200]
              and sum(v['kind'] == 'clear' for v in items) == 1, 'one full clear precedes every rendered frame')
        live = []
        for item in items[2:-1]:
            check(item['kind'] in {'fill', 'text'}, 'only production text and fill events occur after clear')
            rect = item['rect']; check(len(rect) == 4 and all(type(v) is int for v in rect), 'actual integer draw rectangle')
            x, y, width, height = rect
            check(0 <= x <= x + width <= 1440 and 384 <= y < y + height <= 3200,
                  'all postclear UEFI pixels remain below MEM08 reserved rows 0 through 383')
            check(not any(prior_module().rectangles_intersect(rect, old) for old in live),
                  'executed production draw cannot overlap or erase existing text')
            if item['kind'] == 'text':
                value, scale = item['text'], item['scale']
                check(isinstance(value, str) and value.isascii() and type(scale) is int and scale > 0,
                      'actual text uses declared ASCII font input and scale')
                check(width == ((len(value) * 6 - 1) * scale if value else 0) and height == 7 * scale,
                      'actual trace rectangle equals every executed glyph extent')
                if value: live.append(rect)
        summary = items[-1]
        check(summary['forbidden_glyphs'] == 0 and summary['glyph_pixels'] > 10000,
              'real production glyph stores execute within GOP and reserved-region bounds')
    check({'initial', 'read_permit', 'kernel_hash_pass', 'kernel_hash_refused', 'loader_handoff_ready',
           'loader_error_max_values', 'read_refused'} <= names, 'transport, hash, handoff and refusal frames all executed')
    return dict(frame_count=len(frames), event_count=len(events), no_overlap_or_late_erasure=True)


def replay_mem08_stage_render(root, pkg, src, base, receipt, detail, require):
    path = base / 'stage-render-audit.json'; frozen = json.loads(path.read_text())
    pin(path, receipt['stage_render_audit_sha256'], 'bound production renderer proof')
    require(frozen['schema'] == 'thor-mem08-executed-stage-render-event-audit-v1'
            and frozen['result'] == 'PASS_ALL_STAGE_FRAMES_NO_OVERLAP'
            and frozen['xnu_y_reserved'] == [0, 383], 'MEM08 actual source rendering has explicit shifted XNU reservation')
    trace = base / 'stage-render-events.jsonl'; harness = base / 'stage-render-harness.c'
    pin(trace, frozen['host_trace_sha256'], 'bound production draw-event trace')
    pin(harness, frozen['host_harness_sha256'], 'bound production source-render harness')
    independent = executed_draw_events_contract([json.loads(line) for line in trace.read_text().splitlines() if line])
    require(independent['frame_count'] == frozen['frame_count'] and independent['event_count'] == frozen['event_count'],
            'independent event-order, font-geometry and overlap reconstruction matches all rendered frames')
    test = src / 'Platforms/Xiaomi/thorPkg/Tests/test_mem08_stage_render.py'
    pin(test, frozen['test_source_sha256'], 'same actual production renderer test source')
    with tempfile.TemporaryDirectory(prefix='mem08-independent-ui-', dir=pkg.parent) as temp:
        output = Path(temp) / 'stage-render-audit.json'; env = os.environ.copy()
        env['THOR_MEM08_RENDER_AUDIT'] = str(output); env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin'
        run = subprocess.run([sys.executable, '-B', str(test), '-q'], capture_output=True, text=True, env=env, timeout=180)
        require(run.returncode == 0 and output.is_file(), 'fresh compiler executes the complete MEM08 production renderer suite')
        require(same_document(json.loads(output.read_text()), frozen), 'fresh production renderer reproduces exact frozen frames and source semantics')
        require(sha(output.parent / 'stage-render-events.jsonl') == frozen['host_trace_sha256'], 'fresh production draw trace is byte-identical')
    directory = src / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe'
    source = (directory / 'ThorEntryBdsDxe.c').read_text()
    font = source.split('STATIC CONST GLYPH mFont[]', 1)[1].split('\n};', 1)[0]
    glyphs = re.findall(r"\{'(.)',\{([0-9,]+)\}\}", font)
    raw = b''.join(bytes([ord(char), *[int(v) for v in rows.split(',')]]) for char, rows in glyphs)
    pe = src / 'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi'
    require(len(glyphs) > 40 and pe.read_bytes().count(raw) == 1 and sha_bytes(raw) == frozen['font_bitmap_sha256'],
            'actual native PE contains exactly the complete production font exercised by host pixel tests')
    lib = pe.with_suffix('.lib')
    symbols = subprocess.run(['xcrun', 'llvm-nm', '--undefined-only', str(lib)], capture_output=True, text=True, timeout=60)
    require(symbols.returncode == 0 and 'HudTraceText' not in symbols.stdout and b'HudTraceText' not in pe.read_bytes(),
            'real native archive and PE have no host-only draw trace callback')
    return independent


def validate_mem08_transition(root, src, layout, base, receipt, detail, require):
    helper = load_module('mem08_actual_transition_reader', Path(__file__).with_name('transition_bind.py'))
    def raw(tree):
        output = tree / 'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT'
        start, end = helper.transition_symbols(output / 'ThorEntryBdsDxe.map')
        code, meta = helper.load_pe(output / 'ThorEntryBdsDxe.efi', start, end - start)
        require(len(code) == 500 and not meta['relocations_in_transition'], 'actual native PE transition is 500 relocation-free bytes')
        return code
    old, new = raw(root / 'worktrees/uefi/vm-mem07'), raw(src)
    decoded = transition_words_contract(old, new, layout)
    require(True, 'independent actual PE decode binds only allowed MOV-wide immediates to real diagnostic kernel geometry')
    folder = base / 'postlink-transition'; path = folder / 'mem08-transition-binding.json'
    pin(path, receipt['transition_binding_audit_sha256'], 'dedicated MEM08 transition semantic proof identity')
    require(sha(path) == detail['transition_binding_audit_sha256'], 'same transition proof bound by release and detailed firmware proof')
    doc = json.loads(path.read_text())
    require(doc['schema'] == 'thor-mem08-transition-owned-layout-immediate-proof-v1' and doc['phone_actions'] == 0,
            'MEM08 transition proof has distinct reviewed source scope')
    require(doc['old_code_sha256'] == sha_bytes(old) and doc['new_code_sha256'] == sha_bytes(new)
            == receipt['linked_transition_sha256'] and all(doc[k] == v for k, v in decoded.items()),
            'frozen transition proof exactly equals independently decoded raw PE words')
    require((folder / 'old-transition.bin').read_bytes() == old and (folder / 'new-transition.bin').read_bytes() == new,
            'proof raw transition files equal actual linked executable bytes')
    directory = src / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe'
    require(helper.header_expected(directory / 'ThorEntry06TransitionExpected.h')[0].hex() == sha_bytes(new),
            'expected native transition header contains this independently decoded code SHA')
    require((directory / 'thor_entry06_arch.S').read_bytes() ==
            (root / 'worktrees/uefi/vm-mem07/Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/thor_entry06_arch.S').read_bytes(),
            'reviewed handoff assembly source has no new instructions')
    return dict(transition_bytes=500, relocations=0, binding_proof_sha256=sha(path), **decoded)


def validate_mem08_adapter_guards(root, src, base, document, require):
    cases = [('writable', 'WRITABLE', 'mem08-whole-window-writable.json', 'whole_window_writable_audit_sha256', 20,
              'thor-mem08-production-whole-window-writable-host-execution-v1'),
             ('stack', 'STACK', 'mem08-shared-stack.json', 'shared_stack_audit_sha256', 15,
              'thor-mem08-production-shared-stack-and-locate-host-execution-v1')]
    result = {}
    for name, variable, filename, key, count, schema in cases:
        path = base / filename; pin(path, document[key], 'frozen actual full-loader adapter proof: ' + name)
        frozen = json.loads(path.read_text())
        require(frozen['schema'] == schema and frozen['phone_actions'] == 0 and len(frozen['results']) == count,
                'actual production adapter fault matrix has complete honest scope: ' + name)
        test = src / ('Platforms/Xiaomi/thorPkg/Tests/test_mem08_' + name + '.py')
        with tempfile.TemporaryDirectory(prefix='mem08-independent-adapter-' + name + '-', dir=base) as temp:
            output = Path(temp) / 'adapter.json'; env = os.environ.copy(); env['THOR_MEM08_' + variable + '_AUDIT'] = str(output)
            run = subprocess.run([sys.executable, '-B', str(test), '-q'], env=env, capture_output=True, text=True, timeout=120)
            require(run.returncode == 0 and ('Ran %d tests' % count) in run.stderr and output.is_file(),
                    'fresh execution of actual production adapter and its refusal controls: ' + name)
            require(same_document(json.loads(output.read_text()), frozen), 'actual production adapter reproduces every frozen case: ' + name)
        by_mode = {item['mode']: item for item in frozen['results']}
        require(len(by_mode) == count, 'actual adapter cases have unique mode identities: ' + name)
        if name == 'writable':
            require(set(by_mode) == set(range(19)) | {20} and by_mode[0]['pass'] == 1
                    and by_mode[0]['at_calls'] == 57344 and by_mode[0]['gcd_queries'] == 1
                    and all(item['pass'] == int(mode == 0) and item['endpoints_untouched'] is True
                            and 0 <= item['at_calls'] <= 57344 for mode, item in by_mode.items())
                    and by_mode[14]['at_calls'] == 28673 and by_mode[15]['at_calls'] == 57344,
                    'whole 112MiB writable adapter checks every read/write page and rejects middle/last-page faults')
        else:
            require(set(by_mode) == set(range(15)), 'all original shared-stack/locator adapter cases are retained')
            for mode, item in by_mode.items():
                status = 0 if mode in {0, 1, 5, 9} else 101 if mode == 8 else 100
                probe = int(mode in {0, 1, 5, 8, 9})
                require(item['status'] == status and item['probe_calls'] == probe
                        and item['exact_shared_stack_exclusion'] is bool(probe),
                        'actual locator/probe excludes the exact original shared stack and rejects unsafe metadata: ' + str(mode))
        result[name] = {'proof_sha256': sha(path), 'test_source_sha256': sha(test), 'fresh_tests': count}
    return result


def validate_mem08_native_full_loader(root, src, base, receipt, detail, full, require):
    path = base / 'mem08-full-loader-proof.json'; doc = json.loads(path.read_text())
    pin(path, receipt['full_loader_audit_sha256'], 'real full-loader native/host production proof identity')
    require(sha(path) == detail['full_loader_audit_sha256'] and doc['phone_actions'] == 0
            and doc['full_kernel_entry_executed'] is False, 'full-layout probe has no full-kernel entry or phone success claim')
    adapters = validate_mem08_adapter_guards(root, src, base, doc, require)
    expected_objects = {'thor_mem08_full.obj', 'BaseUefiDecompressLib.obj', 'ThorEntryBdsDxe.obj',
                        'thor_entry06_arch.obj', 'thor_mem07_loader.obj'}
    objects = doc['actual_native_objects']
    require(set(objects) == expected_objects, 'all five actual BDS, transition, allocation, loader and decoder native objects are inventoried')
    host = load_module('mem08_actual_archive_reader', Path(__file__).with_name('package_host.py'))
    for name, item in objects.items():
        library = rooted(src, item['library_relative_path']); source = rooted(src, item['source_relative_path'])
        raw = host.archive_member_bytes(library.read_bytes(), name)
        require(len(raw) >= 20 and struct.unpack_from('<H', raw)[0] == 0xaa64 and sha_bytes(raw) == item['object_sha256'],
                'actual linked archive contains the exact ARM64 production object: ' + name)
        pin(source, item['source_sha256'], 'actual native production source: ' + name)
        if name == 'BaseUefiDecompressLib.obj':
            require(item['source_sha256'] == '1eb6bcddb9ab4bafb1a0ed97ab84b5b1038350062ce883ac22876ebccf2c0bce',
                    'native standard decoder is the unmodified algorithm independently replayed before seed')
        names = {'thor_mem08_full.obj': ['ThorMem08Locate', 'ThorMem08Stage'],
                 'BaseUefiDecompressLib.obj': ['UefiDecompress', 'UefiDecompressGetInfo']}.get(name)
        if names:
            symbols = subprocess.run(['xcrun', 'llvm-nm', '--defined-only', str(library)], capture_output=True, text=True, timeout=60)
            require(symbols.returncode == 0 and all(re.search(r'(?m)^\s*[0-9a-fA-F]+\s+[Tt]\s+_?' + symbol + r'\s*$', symbols.stdout)
                    for symbol in names), 'actual native linked library defines required production entry points: ' + name)
    stack_path = base / 'native-stack-usage.json'; pin(stack_path, doc['stack_usage_artifact_sha256'], 'native compiler stack usage proof identity')
    stack = json.loads(stack_path.read_text()); require(stack['phone_actions'] == 0 and
        stack['source_build_commit'] == receipt['uefi_build_source_commit'], 'native stack evidence belongs to actual compile revision')
    reports = stack['reports']; all_usage = ''
    require({'thor_mem08_full.su', 'ThorEntryBdsDxe.su', 'BaseUefiDecompressLib.su', 'thor_mem07_loader.su'} == set(reports)
            and stack['actual_native_objects'] == objects,
            'four native compiler stack outputs bind all five allocation/transition/loader/decoder objects')
    for name, item in reports.items():
        file = rooted(src, item['compiler_output_relative_path']); pin(file, item['sha256'], 'actual compiler stack output: ' + name)
        require(file.stat().st_size == item['bytes'], 'actual stack-report byte length: ' + name)
        all_usage += file.read_text() + '\n'
    require(stack['workspace_static_bytes'] == 1288 and stack['shared_stack_guard_bytes'] == 16384,
            'native 32-record workspace stays retained/static and shared stack guard remains 16KiB')
    frames = stack['new_function_stack_bytes']
    require({'ThorMem08Locate', 'ThorMem08Stage'} <= set(frames) and all(type(n) is int and 0 <= n < 1024 for n in frames.values()),
            'actual production locator/stager and adapter frames stay below 1KiB each')
    for name, frame in frames.items():
        matches = re.findall(r'(?m)^.*:' + re.escape(name) + r'\t([0-9]+)\tstatic\s*$', all_usage)
        require(matches == [str(frame)], 'independent native stack-frame parse: ' + name)
    output = src / 'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT'
    binary = (output / 'ThorEntryBdsDxe.efi').read_bytes()
    header = rooted(root, full['header_path']).read_bytes()
    require(binary.count(header) == 1 and bytes.fromhex(FULL_SPAN_SHA) in binary,
            'real linked PE embeds exactly the header and loaded-span digest of selected public FULL08 asset')
    for text in (b'LDR PLAN', b'FULL LAYOUT %a S%u / FREE %u', b'CHECK ONLY / NO FULL-XNU ENTRY'):
        require(text in binary, 'real PE exposes full-layout stage result: ' + text.decode())
    return {'proof_sha256': sha(path), 'native_object_count': 5, 'native_stack_frames_independently_parsed': len(frames),
            'persistent_diagnostic_window_bytes': 64 << 20, 'temporary_full_layout_window_bytes': WINDOW_BYTES,
            'full_kernel_entry_executed': False, 'hardware_full_layout_verified': False, 'adapter_guards': adapters}


def cached_xnu_precheck(root, path, expected, candidate, xsrc, xproof, require):
    pin(path, expected, 'explicit completed MEM08 native precheck SHA256')
    doc = json.loads(path.read_text())
    require(doc['schema'] == 'thor-mem08-public-xnu-precheck-v1' and doc['result'] == 'PASS_PUBLIC_XNU_PRECHECK'
            and doc['verifier_sha256'] == sha(Path(__file__)) and doc['phone_actions'] == 0
            and doc['seed_generated'] is False and doc['full_L2_ready'] is False,
            'native public precheck comes from exact current verifier without seed or device action')
    require(doc['conditions_count'] == len(doc['checks']) and all(v['passed'] for v in doc['checks']),
            'completed native precheck has all actual conditions PASS')
    pins, sizes = doc['frozen_input_sha256'], doc['frozen_input_bytes']
    check(set(pins) == set(sizes), 'native precheck complete hash and byte inventory')
    for name, digest in pins.items():
        item = rooted(root, name); pin(item, digest, 'native precheck frozen input: ' + item.name)
        check(item.stat().st_size == sizes[name], 'native precheck frozen byte length')
    require(True, 'every frozen diagnostic source, build object, history and machinecode input remains unchanged')
    validate_mem08_frozen_artifact(root, candidate, xsrc, xproof, require)
    return doc


def validate_unmodified_memory_policy(root, src, require):
    previous = root / 'worktrees/uefi/vm-mem07'
    relatives = ['Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/thor_fdt_gate.c',
        'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/thor_fdt_gate.h',
        'Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c']
    for relative in relatives:
        require((src / relative).read_bytes() == (previous / relative).read_bytes(),
                'strict original FDT/DRAM ownership policy remains byte-identical: ' + Path(relative).name)


def verify_release(args):
    root = args.root.resolve(); run_id = args.run_id
    check(run_id == 'thor-20261026-vm-mem08', 'exact unique MEM08 image run identity')
    src, candidate, xsrc, xproof, release, audit_path, uproof, transfer, asset, asset_manifest = (
        rooted(root, p) for p in (args.source, args.candidate, args.xnu_source, args.xnu_proof,
        args.firmware_release, args.firmware_audit, args.uefi_detail_proof, args.public_transfer_index,
        args.full_loader_asset, args.full_loader_manifest))
    pkg = root / 'runs' / run_id / 'l2/package'; output = pkg / 'independent-offline.json'
    check(pkg.is_dir() and (args.check_only or not output.exists()), 'new immutable full L2 output')
    require = Requirements(); prior = prior_module()
    manifest = json.loads((pkg / 'manifest.json').read_text()); receipt = json.loads(release.read_text())
    audit = json.loads(audit_path.read_text()); detail = json.loads(uproof.read_text())
    lock = json.loads((candidate / 'manifest.json').read_text()); built = json.loads((candidate / 'build-manifest.json').read_text())
    layout = json.loads((candidate / 'kernel-layout.json').read_text()); extra = json.loads(xproof.read_text())
    if args.reuse_xnu_precheck or args.xnu_precheck_sha256:
        check(args.reuse_xnu_precheck and args.xnu_precheck_sha256, 'native precheck path and hash must both be supplied')
        native_cache = cached_xnu_precheck(root, rooted(root, args.reuse_xnu_precheck), args.xnu_precheck_sha256,
                                          candidate, xsrc, xproof, require)
        native_result = native_cache['compiled_kernel_frontier']
    else:
        native_cache = None
        native_result, extra = validate_mem08_xnu(root, output, xsrc, candidate, xproof, require)
    require(manifest['schema'] == 'thor-one-use-image-v2' and manifest['run_id'] == run_id,
            'new MEM08 one-use image manifest identity')
    full_precheck = rooted(root, args.full_loader_precheck) if args.full_loader_precheck else None
    check(full_precheck and args.full_loader_precheck_sha256, 'full real production codec/layout precheck is required')
    full_cache = cached_full_loader_precheck(root, full_precheck, args.full_loader_precheck_sha256,
        src, asset, asset_manifest, require)
    full = full_cache['inputs']['full_loader']
    require(manifest['full_loader'] == full, 'package manifest binds exact independently decoded FULL08 public asset')
    for key, name in [('kernel_artifact_manifest_sha256', 'manifest.json'), ('kernel_build_manifest_sha256', 'build-manifest.json')]:
        require(manifest[key] == sha(candidate / name), 'package binds frozen diagnostic build input: ' + name)
    require(manifest['kernel_build_source_revision'] == lock['build_source_commit']
            and manifest['kernel_source_revision'] == receipt['kernel_source_commit'] == lock['xnu_commit'],
            'packaged diagnostic source/build revisions remain distinct and exact')
    boot, kernel = pkg / 'thor-boot.img', candidate / 'kernel.macho'
    fd = src / 'Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd'
    require(boot.is_file() and boot.stat().st_size == manifest['image_bytes'] and boot.stat().st_size < 64 << 20
            and sha(boot) == manifest['image_sha256'], 'actual bounded Boot v4 image has its exact manifest SHA256')
    require(sha(kernel) == manifest['kernel_sha256'] == receipt['kernel_sha256'] == lock['kernel_sha256'],
            'image contains selected new diagnostic kernel, separate from full APFS validation image')
    require(sha(fd) == sha(pkg / 'SILICIUM_UEFI.fd') == receipt['uefi_fd_sha256'] == audit['fd']['sha256']
            and fd.stat().st_size == audit['fd']['bytes'] == receipt['uefi_fd_bytes'] == 3145728,
            'actual built, packaged and audited three-MiB firmware are identical')
    require(audit['status'] == 'PASS' and audit['failures'] == [] and len(audit['volumes']) == 2
            and len(audit['actual_modules']) == 20 and len(audit['unique_link_inputs']) == 97,
            'full firmware audit includes two FVs, twenty native modules and ninety-seven input archives')
    pin(audit_path, receipt['firmware_audit_sha256'], 'release binds complete actual firmware audit')
    uefi_head = subprocess.check_output(['git', '-C', str(src), 'rev-parse', 'HEAD'], text=True).strip()
    require(uefi_head == manifest['uefi_commit'] == receipt['uefi_commit']
            and not subprocess.check_output(['git', '-C', str(src), 'status', '--porcelain'], text=True).strip(),
            'actual final firmware source is the exact clean release revision')
    profile = dict(lock, _validated_packaging_profile='MEM08', _validated_candidate_directory=str(candidate))
    shared = load_module('mem08_shared_release_validator', Path(__file__).with_name('package_mem08.py'))
    shared.validate_release(root, src, release, audit_path, profile, receipt, audit)
    shared.validate_native_log(candidate, xsrc, lock, built, extra)
    require(True, 'strict complete MEM08 release/source/UI/log-guard/asset preseed bindings replayed')
    require(receipt['full_loader'] == detail['full_loader'] == full,
            'actual native firmware release and detail bind selected exact full-loader asset')
    validate_unmodified_memory_policy(root, src, require)
    pe = src / 'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi'
    require(mem08_pe_identity(pe.read_bytes()), 'actual linked native PE contains only MEM08 run and memory-diagnostic identity')
    for text in (b'THOR / XNU', b'CURRENT RESULT', b'UEFI CHECKS PASS', b'UEFI CHECKS FAILED',
                 b'ESR / ELR / FAR', b'NOT CAPTURED IN UEFI', b'UEFI 2 KERNEL HASH PASS'):
        require(text in pe.read_bytes(), 'actual native PE retains explicit diagnostic state: ' + text.decode())
    prov = src / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/VM_POST_TTBR1_PROVENANCE.json'
    binding = json.loads(prov.read_text()); geometry = binding['generated_layout']
    require(binding['kernel_sha256'] == sha(kernel) and binding['loaded_span_sha256'] == layout['loaded_span_sha256']
            and geometry['E09_KERNEL_SPAN'] == layout['loaded_span_bytes']
            and geometry['E06_ENTRY_OFFSET'] == layout['entry_offset'] and geometry['E06_VECTOR_OFFSET'] == layout['vectors_offset'],
            'actual generated handoff geometry binds this diagnostic kernel and loaded bytes')
    require(geometry['E06_ARGS_OFFSET'] == geometry['E09_KERNEL_SPAN']
            and geometry['E06_FINAL_USED'] == geometry['E06_TRANSITION_OFFSET'] + 0x44000
            and geometry['E09_ADT_OFFSET'] == geometry['E06_FINAL_USED'] - 0x4000
            and geometry['E06_FINAL_USED'] < 64 << 20,
            'diagnostic bootargs, ADT, transition and stacks stay within independent retained 64MiB grant')
    transition = validate_mem08_transition(root, src, layout, release.parent, receipt, detail, require)
    renderer = replay_mem08_stage_render(root, pkg, src, release.parent, receipt, detail, require)
    loader = validate_mem08_native_full_loader(root, src, release.parent, receipt, detail, full, require)
    bundle = (pkg / 'kernel-bundle.bin').read_bytes()
    require(sha_bytes(bundle) == manifest['bundle_sha256'] and len(bundle) == manifest['bundle_bytes'],
            'complete dual-role payload bundle length and SHA256 match one-use manifest')
    wire = full08_bundle_contract(bundle, kernel.read_bytes(), asset.read_bytes(), rooted(root, full['header_path']).read_bytes(), manifest['seed_sha256'])
    require(True, 'independent exact primary/auxiliary envelope reparse preserves separate kernel roles and all zero padding')
    prior.validate_boot_bytes(boot.read_bytes(), (pkg / 'kernel.gz-dtb').read_bytes(), bundle)
    require(True, 'independent exact Boot v4 parser verifies image sections and padding')
    require((pkg / 'unpacked/ramdisk').read_bytes() == bundle and
            (pkg / 'unpacked/kernel').read_bytes() == (pkg / 'kernel.gz-dtb').read_bytes(), 'AOSP unpacker agrees with actual kernel and full transport bytes')
    v4 = json.loads((pkg / 'verify-image.stdout').read_text())
    require(v4['ok'] and v4['header_version'] == 4 and v4['file_sha256'] == sha(boot) and not v4['warnings'],
            'separate pinned AOSP image verifier accepts without warnings')
    require(manifest['seed_source'] in {'linux-os.getrandom(flags=0)', 'darwin-os.urandom'}
            and manifest['seed_bytes'] == 64 and manifest['seed_raw_logged'] is False and manifest['seed_reuse_allowed'] is False,
            'one-use seed is generated by declared OS CSPRNG and never logged or reused')
    tools = manifest['package_tools']; interpreter = tools['python']
    require(interpreter['path'] == str(Path(sys.executable).resolve()) and interpreter['version'] == list(sys.version_info[:3]),
            'packaging interpreter executable and version are exact')
    pin(Path(interpreter['path']), interpreter['sha256'], 'same packaging Python executable bytes')
    pin(Path(__file__).with_name('package.py'), tools['packager_sha256'], 'same reviewed image packager source')
    pin(Path(__file__).with_name('package_host.py'), tools['helper_sha256'], 'same reviewed packaging preflight helper source')
    require(all(c['returncode'] == 0 for c in manifest['package_commands']), 'all genuine packaging subprocesses succeeded')
    stock = tomllib.loads((root / 'thor-build.toml').read_text())['stock']
    shim, dtb = (pkg / 'BootShim.bin').read_bytes(), (pkg / 'thor-stock-vendor-dtb.bin').read_bytes()
    require(len(shim) == 112 and sha_bytes(shim) == stock['bootshim_sha256']
            and len(dtb) == 6443651 and sha_bytes(dtb) == stock['dtb_sha256'], 'immutable stock BootShim and vendor DTB bytes are retained')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS); unpacked = decoder.decompress((pkg / 'kernel.gz-dtb').read_bytes())
    require(decoder.eof and not decoder.unconsumed_tail and unpacked == shim + fd.read_bytes() and decoder.unused_data == dtb,
            'actual gzip reconstructs exact shim/FD followed by unchanged stock DTB')
    historical = []
    for directory in (root / 'runs', root / 'evidence/build'):
        if not directory.is_dir(): continue
        for path in directory.rglob('manifest.json'):
            if path.resolve() == (pkg / 'manifest.json').resolve(): continue
            try: item = json.loads(path.read_text())
            except (ValueError, UnicodeError): continue
            if not isinstance(item, dict): continue
            if item.get('run_id') == run_id:
                require(item.get('image_sha256') == sha(boot) and item.get('seed_sha256') == manifest['seed_sha256'],
                        'same-run package mirrors have identical new image and seed digests')
            else: historical.append(item)
    prior.reject_reuse(sha(boot), manifest['seed_sha256'], historical, set(prior.CONSUMED_BASELINES) | {MEM07_IMAGE})
    require(True, 'new image and seed digest differ from every historical package including consumed MEM07')
    require(not (pkg / 'ONE_SHOT_USED-DO-NOT-BOOT-AGAIN').exists() and not (pkg / 'INVALID-NOT-FOR-PHONE').exists()
            and not (root / 'state/consumed-images' / (sha(boot) + '.json')).exists()
            and not (root / 'runs' / run_id / 'l3').exists(), 'new MEM08 has no consumption marker, quarantine or L3 execution')
    adapter = load_module('mem08_complete_firmware_replay', Path(__file__).with_name('firmware_audit_replay.py'))
    with tempfile.TemporaryDirectory(prefix='mem08-independent-firmware-', dir=pkg.parent) as temp:
        fresh_firmware = adapter.replay(root, src, audit_path, transfer, Path(temp))
    require(fresh_firmware['result'] == 'PASS' and fresh_firmware['modules'] == 20 and fresh_firmware['volumes'] == 2
            and fresh_firmware['link_inputs'] == 97, 'fresh complete FV/FFS/LZMA, twenty PE modules and all native archive/input checks succeed')
    report = dict(schema='thor-vm-independent-offline-gate-v1', checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        result='PASS', run_id=run_id, verifier_sha256=sha(Path(__file__)), conditions_count=len(require.marks), checks=require.marks,
        validation_timing='postpackage fresh validation',
        inputs={'android_boot_v4': {'sha256': sha(boot), 'bytes': boot.stat().st_size},
                'native_xnu': {'sha256': sha(kernel), 'bytes': kernel.stat().st_size},
                'uefi_fd': {'sha256': sha(fd), 'bytes': fd.stat().st_size}, 'full_loader': full},
        xnu_mem08_proof_sha256=sha(xproof), uefi_mem08_proof_sha256=sha(uproof),
        requires_new_recording_authorization=True, L3_authorized=False, compiled_kernel_frontier=native_result,
        loader_contract=loader, production_codec_replay=full_cache['production_codec_replay'],
        production_inplace_replay=full_cache['production_inplace_replay'], production_fault_replay=full_cache['production_fault_replay'],
        production_sha_replay=full_cache['production_sha_replay'],
        reused_completed_XNU_precheck=None if native_cache is None else {'path':str(rooted(root,args.reuse_xnu_precheck)),
            'sha256':args.xnu_precheck_sha256, 'conditions_count':native_cache['conditions_count'], 'fresh_reexecution_claimed':False},
        reused_completed_full_loader_precheck={'path':str(full_precheck), 'sha256':args.full_loader_precheck_sha256,
            'conditions_count':full_cache['conditions_count'], 'fresh_reexecution_claimed':False},
        fresh_firmware_replay=fresh_firmware, executed_stage_render=renderer, transition_rebind=transition, wire_contract=wire,
        device_memory_ownership_granted=False, ready_for_one_use_temporary_boot=True,
        device_commands=0, hardware_test_attempts=0, full_kernel_entry_executed=False)
    if not args.check_only:
        with output.open('x') as stream: json.dump(report, stream, indent=2); stream.write('\n')
    print(json.dumps({'result':'PASS', 'conditions_count':len(require.marks), 'image_sha256':sha(boot), 'phone_actions':0}))
    return 0


def main():
    if '--precheck-xnu' in sys.argv: return public_precheck_main('precheck-xnu')
    if '--precheck-full-loader' in sys.argv: return public_precheck_main('precheck-full-loader')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'candidate', 'source', 'firmware-release', 'firmware-audit', 'xnu-source',
                 'xnu-proof', 'uefi-detail-proof', 'public-transfer-index', 'full-loader-asset', 'full-loader-manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--reuse-xnu-precheck', type=Path); parser.add_argument('--xnu-precheck-sha256')
    parser.add_argument('--full-loader-precheck', type=Path, required=True)
    parser.add_argument('--full-loader-precheck-sha256', required=True)
    parser.add_argument('--check-only', action='store_true')
    try: return verify_release(parser.parse_args())
    except Exception as error:
        print(json.dumps({'result':'FAIL', 'error_type':type(error).__name__, 'error':str(error), 'phone_actions':0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
