"""Validate the public MEM08 FULL08 input before creating any one-use seed."""
import hashlib
import json
from pathlib import Path
import struct

MAGIC = b'THOR-FULL08' + b'\0' * 5
HEADER = struct.Struct('<16sIIQ32sQQ32sIIQ')
RAW_SHA256 = 'a798cf368ab013e7389f3c5944cea9381859fb98238b346b33423b00bf8d3a37'
RAW_BYTES = 50659328
SPAN_BYTES = 113229824
MINIMUM_BYTES = 113250304
WINDOW_BYTES = 112 * 1024 * 1024
MAX_COMPRESSED_BYTES = 20 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise RuntimeError('FULL08: ' + message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rooted(root, declared):
    value = (Path(root) / declared).resolve()
    require(value.is_relative_to(Path(root).resolve()), 'path escapes project')
    return value


def parse_header(data):
    require(len(data) == HEADER.size == 128, 'outer header is exactly128 bytes')
    magic, version, header, size, digest, rawsize, span, rawsha, algorithm, count, reserved = HEADER.unpack(data)
    require((magic, version, header, algorithm, count, reserved) ==
            (MAGIC, 1, 128, 1, 15, 0), 'version/algorithm/segment count/reserved')
    require(0 < size <= MAX_COMPRESSED_BYTES, 'bounded EFI-compressed body')
    require((rawsize, span, rawsha.hex()) == (RAW_BYTES, SPAN_BYTES, RAW_SHA256),
            'the fixed original20G81 full kernel and span')
    return dict(compressed_bytes=size, compressed_sha256=digest.hex(), raw_bytes=rawsize,
                raw_sha256=rawsha.hex(), loaded_span_bytes=span, algorithm_id=algorithm,
                nonzero_segment_count=count)


def macho_layout(raw):
    require(len(raw) >= 32, 'complete Mach-O64 header')
    magic, cpu, _, filetype, ncmds, commandbytes, _, _ = struct.unpack_from('<8I', raw)
    require(magic == 0xfeedfacf and cpu == 0x100000c and filetype == 2, 'arm64 executable Mach-O')
    require(0 < ncmds <= 512 and 8 * ncmds <= commandbytes <= 0x100000
            and commandbytes <= len(raw) - 32, 'bounded complete load commands')
    cursor = 32
    end = 32 + commandbytes
    records = []
    for _ in range(ncmds):
        require(cursor + 8 <= end, 'load command header')
        command, size = struct.unpack_from('<II', raw, cursor)
        require(size >= 8 and size % 8 == 0 and size <= end - cursor, 'load command span')
        if command == 0x19:
            require(size >= 72, 'segment64 header')
            name = raw[cursor+8:cursor+24].split(b'\0', 1)[0].decode('ascii')
            va, vm, offset, count, maximum, initial, sections, flags = struct.unpack_from('<4Q4I', raw, cursor+24)
            require(size == 72 + 80 * sections, 'segment section extent')
            require(count <= vm and offset <= len(raw) and count <= len(raw) - offset,
                    'segment file span and VM capacity')
            require(vm <= (1 << 64) - va and (vm != 0 or count == 0), 'segment VA overflow')
            records.append(dict(name=name, virtual_address=va, virtual_size=vm,
                                file_offset=offset, file_bytes=count))
        cursor += size
    require(cursor == end, 'exact load command end')
    live = [r for r in records if r['virtual_size']]
    require(live, 'nonempty loaded image')
    base = min(r['virtual_address'] for r in live)
    top = max(r['virtual_address'] + r['virtual_size'] for r in live)
    require(0 < top - base <= WINDOW_BYTES, 'loaded span inside temporary window')
    for row in records:
        row['destination_offset'] = row['virtual_address'] - base if row['virtual_size'] else 0
    by_vm = sorted(live, key=lambda r: r['destination_offset'])
    for a, b in zip(by_vm, by_vm[1:]):
        require(a['destination_offset'] + a['virtual_size'] <= b['destination_offset'],
                'nonoverlapping loaded segments')
    by_file = sorted((r for r in live if r['file_bytes']), key=lambda r: r['file_offset'])
    require(by_file, 'file-backed input segments')
    for row in by_file:
        require(row['destination_offset'] >= row['file_offset'], 'inplace expansion moves forward')
    for a, b in zip(by_file, by_file[1:]):
        require(a['file_offset'] + a['file_bytes'] <= b['file_offset'], 'file ranges do not overlap')
        require(a['destination_offset'] + a['virtual_size'] <= b['destination_offset'],
                'source and destination orders support reverse memmove')
    loaded = bytearray(top - base)
    for row in by_file:
        d, s, count = row['destination_offset'], row['file_offset'], row['file_bytes']
        loaded[d:d+count] = raw[s:s+count]
    return dict(virtual_base=base, virtual_end=top, loaded_span_bytes=top-base,
                loaded_span_sha256=sha(loaded), segment_record_count=len(records),
                nonzero_segment_count=len(live), segments=records,
                sorted_nonzero_segments=by_vm)


def validate(root, asset_path, manifest_path):
    root = Path(root).resolve()
    asset_path, manifest_path = rooted(root, asset_path), rooted(root, manifest_path)
    document = json.loads(manifest_path.read_text())
    require(document.get('schema') == 'thor-mem08-full-loader-asset-v1', 'manifest schema')
    require(rooted(root, document['compressed_path']) == asset_path, 'selected compressed file')
    header_path = rooted(root, document['header_path'])
    raw_path = rooted(root, document['raw_kernel_path'])
    require(header_path.parent == asset_path.parent == manifest_path.parent,
            'header/body/manifest form one public artifact')
    body, header, raw = asset_path.read_bytes(), header_path.read_bytes(), raw_path.read_bytes()
    fields = parse_header(header)
    require(len(body) == fields['compressed_bytes'] and sha(body) == fields['compressed_sha256'],
            'exact compressed bytes and digest')
    require(len(body) >= 8 and struct.unpack_from('<II', body) == (len(body)-8, RAW_BYTES),
            'standard EFI inner framing matches outer sizes')
    require(len(raw) == RAW_BYTES and sha(raw) == RAW_SHA256, 'actual original20G81 raw file')
    geometry = macho_layout(raw)
    require((geometry['segment_record_count'], geometry['nonzero_segment_count'],
             geometry['loaded_span_bytes']) == (18, 15, SPAN_BYTES), 'actual18 records/15 loaded segments')
    for key, value in fields.items():
        require(document.get(key) == value, 'manifest/header agreement: ' + key)
    for key in ('segment_record_count', 'nonzero_segment_count', 'loaded_span_bytes', 'loaded_span_sha256'):
        require(document.get(key) == geometry[key], 'manifest/actual Mach-O agreement: ' + key)
    require(document.get('segments') == geometry['segments'], 'all18 exact segment descriptors')
    require(document.get('sorted_nonzero_segments') == geometry['sorted_nonzero_segments'],
            'sorted15 actual loaded descriptors')
    require(document.get('header_sha256') == sha(header), 'exact header digest')
    require(document.get('minimum_layout_bytes') == MINIMUM_BYTES and
            document.get('scratch_offset') == MINIMUM_BYTES and
            document.get('window_bytes') == WINDOW_BYTES and
            document.get('scratch_reserved_bytes') == 0x4000 and
            type(document.get('scratch_bytes')) is int and 0 < document['scratch_bytes'] <= 0x4000
            and MINIMUM_BYTES + 0x4000 <= WINDOW_BYTES, 'scratch stays after prospective handoff tail')
    return dict(schema='thor-mem08-full-loader-validated-input-v1',
                manifest_path=str(manifest_path.relative_to(root)), manifest_sha256=sha(manifest_path.read_bytes()),
                compressed_path=str(asset_path.relative_to(root)), header_path=str(header_path.relative_to(root)),
                header_sha256=sha(header), **fields, loaded_span_sha256=geometry['loaded_span_sha256'],
                minimum_layout_bytes=MINIMUM_BYTES, scratch_bytes=document['scratch_bytes'],
                scratch_reserved_bytes=0x4000, scratch_offset=MINIMUM_BYTES, window_bytes=WINDOW_BYTES,
                segment_record_count=18, full_kernel_entry_executed=False)
