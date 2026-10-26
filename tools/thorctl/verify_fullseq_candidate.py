#!/usr/bin/env python3
"""Independent FULLSEQ01 input and one-use image verifier.

Parses transport, Mach-O commands, symbols and loaded bytes itself.  Firmware,
native and host replay receipts are bound separately; structural parsing alone
never authorizes packaging or asserts that iOS ran.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import zlib
from datetime import datetime, timezone

OUTER = struct.Struct('<16sIIQ32s64s')
INNER = struct.Struct('<16sIIQ32sQQ32sIIQ')
OUTER_MAGIC = b'THOR-FULLSEQ01\0\0'
INNER_MAGIC = b'THOR-FULL08' + b'\0' * 5
MAX_COMPRESSED = 20 << 20
MAX_RAW = 64 << 20
MAX_WINDOW = 112 << 20
ZERO = bytes(1 << 20)
SYMBOL_NAMES = ('_LowExceptionVectorBase', '_ExceptionVectorsBase', '_bootstrap_pagetables')
STAGES = ['ENTRY', 'ABI_ADT', 'PRE_VM', 'VM_RETURN', 'CONSOLE', 'PLATFORM',
          'MACH_START', 'VM_BOOTSTRAP', 'SCHED', 'IOKIT', 'IRQ_SLEEP', 'UFS',
          'MEDIA', 'ROOT_MOUNT', 'PID1', 'USER_EXEC']
NATURAL_CALLS = {
    'arm_init': ['thor_seq_init', 'PE_init_platform', 'thor_full_before_vm', 'arm_vm_init',
                 'thor_full_after_vm', 'PE_create_console', 'PE_init_printf',
                 'PE_init_platform', 'machine_startup'],
    'kernel_bootstrap': ['vm_mem_bootstrap', 'sched_init'],
    'kernel_bootstrap_thread': ['sched_startup', 'PE_init_iokit', 'bsd_init'],
    'panic_trap_to_debugger': ['thor_seq_panic', 'ml_panic_trap_to_debugger'],
    'bsdinit_task': ['load_init_program'],
    'thor_full_after_vm': ['thor_seq_rebind', 'ml_io_map_wcomb', 'thor_seq_set_framebuffer'],
    'thor_full_failure_hold': ['thor_seq_stop'],
}


class GateError(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise GateError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_command(checks, command, log, *, cwd=None, env=None, timeout=240):
    log = Path(log)
    checks.require(not log.exists(), 'fresh execution log cannot replace old evidence: ' + str(log))
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('x') as stream:
        process = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                                 stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    checks.require(process.returncode == 0, 'fresh host execution succeeds: ' + log.name)
    checks.freeze(log)
    return log


def rooted(root, declared):
    result = (Path(root) / declared).resolve()
    require(result.is_relative_to(Path(root).resolve()), 'input stays within project root')
    return result


def parse_inner(data):
    require(len(data) >= INNER.size + 8, 'complete inner header and standard codec header')
    magic, version, header, count, compressed_hash, raw_bytes, span, raw_hash, algorithm, segments, reserved = INNER.unpack_from(data)
    require((magic, version, header, algorithm, reserved) == (INNER_MAGIC, 1, 128, 1, 0),
            'inner FULL08 identity, EFI standard codec, no reserved bits')
    require(8 < count <= MAX_COMPRESSED and count == len(data) - 128,
            'exact inner compressed body length')
    require(0 < raw_bytes <= MAX_RAW and raw_bytes <= span <= MAX_WINDOW,
            'raw and loaded kernel fit the owned allocation')
    require(0 < segments <= 32, 'bounded nonzero segment count')
    body = data[128:]
    require(sha(body) == compressed_hash.hex(), 'actual compressed body SHA')
    codec_bytes, codec_output = struct.unpack_from('<II', body)
    require(codec_bytes == count - 8 and codec_output == raw_bytes,
            'real standard EFI compression framing')
    return dict(compressed_bytes=count, compressed_sha256=compressed_hash.hex(),
                raw_bytes=raw_bytes, raw_sha256=raw_hash.hex(), loaded_span_bytes=span,
                algorithm_id=algorithm, nonzero_segment_count=segments)


def parse_packet(bundle):
    require(len(bundle) >= 4096 and len(bundle) % 4096 == 0, 'v3 packet uses complete pages')
    magic, version, header, payload_bytes, payload_hash, seed = OUTER.unpack_from(bundle)
    require((magic, version, header) == (OUTER_MAGIC, 3, 128), 'only FULLSEQ01 v3 outer identity')
    require(136 <= payload_bytes <= MAX_COMPRESSED + 128 and payload_bytes <= len(bundle) - 128,
            'complete bounded v3 body')
    require(len(bundle) == ((128 + payload_bytes + 4095) // 4096) * 4096,
            'exact page-rounded packet size')
    payload = bundle[128:128 + payload_bytes]
    require(not any(bundle[128 + payload_bytes:]), 'packet has only zero tail padding')
    require(any(seed), 'nonzero one-use 64-byte seed')
    require(sha(payload) == payload_hash.hex(), 'actual outer body SHA')
    fields = parse_inner(payload)
    return dict(**fields, packet_version=version, packet_header_bytes=header,
                payload_bytes=payload_bytes, payload_sha256=payload_hash.hex(),
                bundle_bytes=len(bundle), bundle_sha256=sha(bundle), seed_bytes=len(seed),
                seed_sha256=sha(seed))


def parse_boot_v4(image, kernel, bundle):
    require(4096 <= len(image) < 64 << 20 and image[:8] == b'ANDROID!',
            'bounded actual Android boot image magic')
    kernel_bytes, ramdisk_bytes, android_os, header_bytes = struct.unpack_from('<4I', image, 8)
    require(struct.unpack_from('<I', image, 40)[0] == 4 and header_bytes == 1584,
            'exact Android boot v4 header ABI')
    require(android_os == 0x18000189 and not any(image[24:40]) and not any(image[44:1580]),
            'reviewed wrapper Android version/patch, zero reserved words and empty cmdline')
    require(struct.unpack_from('<I', image, 1580)[0] == 0 and not any(image[1584:4096]),
            'no boot signature and exact zero header padding')
    require(kernel_bytes == len(kernel) and ramdisk_bytes == len(bundle),
            'actual boot sections have exact selected byte lengths')
    ramdisk_at = 4096 + ((kernel_bytes + 4095) // 4096) * 4096
    end = ramdisk_at + ((ramdisk_bytes + 4095) // 4096) * 4096
    require(end == len(image), 'actual boot image has no extra pages or trailer')
    require(image[4096:4096 + kernel_bytes] == kernel
            and not any(image[4096 + kernel_bytes:ramdisk_at]), 'exact kernel and zero kernel padding')
    require(image[ramdisk_at:ramdisk_at + ramdisk_bytes] == bundle
            and not any(image[ramdisk_at + ramdisk_bytes:end]), 'exact ramdisk and zero ramdisk padding')
    return dict(header_version=4, header_bytes=header_bytes, image_bytes=len(image),
                image_sha256=sha(image), kernel_bytes=kernel_bytes, ramdisk_bytes=ramdisk_bytes,
                signature_bytes=0, cmdline_empty=True, strict_padding=True)


class Checks:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.rows = []
        self.inputs = {}

    def require(self, condition, message):
        self.rows.append(dict(name=message, passed=bool(condition)))
        require(condition, message)

    def freeze(self, declared, expected=None, size=None):
        path = rooted(self.root, declared)
        self.require(path.is_file(), 'actual public input exists: ' + str(path))
        before = path.stat()
        record = dict(bytes=before.st_size, sha256=hash_file(path))
        after = path.stat()
        self.require((before.st_size, before.st_mtime_ns, before.st_ino) ==
                     (after.st_size, after.st_mtime_ns, after.st_ino),
                     'public input stayed stable during hashing: ' + str(path))
        if expected is not None:
            self.require(record['sha256'] == expected, 'exact public input SHA: ' + str(path))
        if size is not None:
            self.require(record['bytes'] == size, 'exact public input bytes: ' + str(path))
        old = self.inputs.get(str(path))
        self.require(old is None or old == record, 'same input has one immutable identity: ' + str(path))
        self.inputs[str(path)] = record
        return path

    def document(self, declared, expected=None):
        path = self.freeze(declared, expected)
        result = json.loads(path.read_text())
        self.require(isinstance(result, dict), 'public JSON is an object: ' + str(path))
        return result, path

    def inventory(self):
        return dict(frozen_input_sha256={name: row['sha256'] for name, row in self.inputs.items()},
                    frozen_input_bytes={name: row['bytes'] for name, row in self.inputs.items()})


def hash_zeros(digest, count):
    require(count >= 0, 'nonnegative zero-fill extent')
    while count:
        amount = min(count, len(ZERO))
        digest.update(ZERO[:amount])
        count -= amount


def parse_macho(raw, *, require_vectors=True):
    require(32 <= len(raw) <= MAX_RAW, 'bounded complete Mach-O64')
    magic, cpu, subtype, kind, ncmds, command_bytes, flags, reserved = struct.unpack_from('<8I', raw)
    require((magic, cpu, subtype, kind, reserved) == (0xfeedfacf, 0x100000c, 0, 2, 0),
            'ordinary arm64 executable Mach-O header')
    require(flags & 1, 'linked kernel has no undefined symbols')
    require(0 < ncmds <= 512 and 8 * ncmds <= command_bytes <= min(1 << 20, len(raw) - 32),
            'bounded command count and byte span')
    cursor, end = 32, 32 + command_bytes
    records, threads, symtabs = [], [], []
    for _ in range(ncmds):
        require(cursor + 8 <= end, 'complete load command prefix')
        command, size = struct.unpack_from('<II', raw, cursor)
        require(size >= 8 and size % 8 == 0 and size <= end - cursor, 'load command bounds/alignment')
        if command == 0x19:
            require(size >= 72, 'complete LC_SEGMENT_64')
            encoded = raw[cursor + 8:cursor + 24].split(b'\0', 1)[0]
            try:
                name = encoded.decode('ascii')
            except UnicodeDecodeError as exc:
                raise GateError('non-ASCII segment name') from exc
            va, vm, file_offset, file_bytes, maximum, initial, sections, section_flags = struct.unpack_from('<4Q4I', raw, cursor + 24)
            require(size == 72 + sections * 80 and name, 'segment section count/name')
            require(file_bytes <= vm and file_offset <= len(raw) and file_bytes <= len(raw) - file_offset,
                    'complete file-backed segment inside VM extent')
            require(va + vm <= 1 << 64 and (vm > 0 or file_bytes == 0), 'segment virtual-address overflow')
            require(initial & ~maximum == 0 and maximum & ~7 == 0, 'segment permission subset')
            for section in range(sections):
                at = cursor + 72 + section * 80
                section_va, section_size = struct.unpack_from('<QQ', raw, at + 32)
                require(not section_size or va <= section_va <= section_va + section_size <= va + vm,
                        'section stays inside its segment')
            records.append(dict(name=name, virtual_address=va, virtual_size=vm,
                                file_offset=file_offset, file_bytes=file_bytes,
                                initial_protection=initial, maximum_protection=maximum))
        elif command == 5:
            require(size == 288 and struct.unpack_from('<II', raw, cursor + 8) == (6, 68),
                    'LC_UNIXTHREAD is an exact arm_thread_state64 record')
            threads.append(struct.unpack_from('<Q', raw, cursor + 272)[0])
        elif command == 2:
            require(size == 24, 'exact LC_SYMTAB command')
            symtabs.append(struct.unpack_from('<4I', raw, cursor + 8))
        cursor += size
    require(cursor == end and len(threads) == 1 and len(symtabs) == 1,
            'exact load-command end and one entry/symbol table')
    live = [row for row in records if row['virtual_size']]
    require(live, 'nonempty loaded image')
    base = min(row['virtual_address'] for row in live)
    top = max(row['virtual_address'] + row['virtual_size'] for row in live)
    require(0 < top - base <= MAX_WINDOW, 'loaded span fits fixed owned window')
    for row in records:
        row['destination_offset'] = row['virtual_address'] - base if row['virtual_size'] else 0
    by_vm = sorted(live, key=lambda row: row['destination_offset'])
    for first, second in zip(by_vm, by_vm[1:]):
        require(first['destination_offset'] + first['virtual_size'] <= second['destination_offset'],
                'no overlapping loaded segments')
    file_rows = sorted((row for row in live if row['file_bytes']), key=lambda row: row['file_offset'])
    require(file_rows, 'kernel has file-backed segments')
    for row in file_rows:
        require(row['destination_offset'] >= row['file_offset'], 'inplace segment destination never precedes source')
    for first, second in zip(file_rows, file_rows[1:]):
        require(first['file_offset'] + first['file_bytes'] <= second['file_offset'], 'file segments do not overlap')
        require(first['destination_offset'] + first['virtual_size'] <= second['destination_offset'],
                'reverse memmove ordering follows actual segment geometry')
    entry = threads[0]
    containing = [row for row in file_rows if row['virtual_address'] <= entry < row['virtual_address'] + row['file_bytes']]
    require(len(containing) == 1 and containing[0]['initial_protection'] & 4 and entry % 4 == 0,
            'thread PC is aligned inside one executable file-backed segment')
    symbol_offset, symbol_count, string_offset, string_bytes = symtabs[0]
    require(symbol_offset <= len(raw) and symbol_count <= (len(raw) - symbol_offset) // 16
            and string_offset <= len(raw) and string_bytes <= len(raw) - string_offset,
            'complete bounded nlist64/string tables')
    found = {name: set() for name in SYMBOL_NAMES}
    for index in range(symbol_count):
        sx, symbol_type, section, description, value = struct.unpack_from('<IBBHQ', raw, symbol_offset + index * 16)
        if symbol_type & 0xe0 or symbol_type & 0x0e != 0x0e:
            continue
        require(sx < string_bytes, 'defined symbol name stays in string table')
        finish = raw.find(b'\0', string_offset + sx, string_offset + string_bytes)
        require(finish >= 0, 'defined symbol name terminates')
        name = raw[string_offset + sx:finish]
        for wanted in SYMBOL_NAMES:
            if name == wanted.encode():
                found[wanted].add(value)
    symbols = {}
    for name, values in found.items():
        if require_vectors:
            require(len(values) == 1, 'one actual selected symbol: ' + name)
        if values:
            require(len(values) == 1, 'selected symbol cannot have two addresses')
            value = next(iter(values))
            require(base <= value < top, 'selected symbol lies inside loaded image')
            symbols[name] = value
    if require_vectors:
        low, runtime, bootstrap = [symbols[name] for name in SYMBOL_NAMES]
        require(low != runtime and low % 2048 == 0 and runtime % 2048 == 0,
                'distinct aligned low and runtime exception vector tables')
        for value in (low, runtime):
            matches = [row for row in file_rows if row['virtual_address'] <= value
                       and value + 2048 <= row['virtual_address'] + row['file_bytes'] and row['initial_protection'] & 4]
            require(len(matches) == 1, 'each actual vector table is executable and file backed')
        require(bootstrap % 4096 == 0, 'bootstrap page-table storage is page aligned')
        require(any(row['virtual_address'] <= bootstrap < row['virtual_address'] + row['virtual_size'] for row in live),
                'bootstrap page-table symbol belongs to loaded storage')
    digest, position = hashlib.sha256(), 0
    for row in by_vm:
        hash_zeros(digest, row['destination_offset'] - position)
        start = row['file_offset']
        digest.update(memoryview(raw)[start:start + row['file_bytes']])
        hash_zeros(digest, row['virtual_size'] - row['file_bytes'])
        position = row['destination_offset'] + row['virtual_size']
    require(position == top - base, 'loaded-span hash covers exact virtual extent')
    result = dict(raw_bytes=len(raw), raw_sha256=sha(raw), virtual_base=base, virtual_end=top,
                  entry_address=entry, entry_offset=entry - base,
                  loaded_span_bytes=top - base, loaded_span_sha256=digest.hexdigest(),
                  segment_record_count=len(records), nonzero_segment_count=len(live),
                  segments=[{key: value for key, value in row.items()
                             if key not in ('initial_protection', 'maximum_protection')} for row in records],
                  symbols=symbols)
    if require_vectors:
        result.update(vector_offset=symbols['_LowExceptionVectorBase'] - base,
                      low_vector_offset=symbols['_LowExceptionVectorBase'] - base,
                      runtime_vector_offset=symbols['_ExceptionVectorsBase'] - base,
                      boot_pt_offset=symbols['_bootstrap_pagetables'] - base)
    return result


def inspect_asset(root, manifest_path):
    root = Path(root).resolve()
    manifest_path = rooted(root, manifest_path)
    document = json.loads(manifest_path.read_text())
    require(document.get('schema') == 'thor-fullseq01-full-kernel-asset-manifest-v1', 'actual FULLSEQ01 asset manifest')
    paths = {role: rooted(root, document[key]) for role, key in
             (('raw', 'raw_path'), ('compressed', 'compressed_path'), ('header', 'header_path'))}
    raw, body, header = [paths[role].read_bytes() for role in ('raw', 'compressed', 'header')]
    require(len(header) == 128, 'inner header is exactly 128 bytes')
    inner = parse_inner(header + body)
    geometry = parse_macho(raw)
    require(inner['raw_bytes'] == len(raw) and inner['raw_sha256'] == sha(raw),
            'inner header binds actual raw Mach-O')
    for key in ('loaded_span_bytes', 'nonzero_segment_count'):
        require(inner[key] == geometry[key], 'inner header agrees with actual geometry: ' + key)
    for key, value in inner.items():
        require(document.get(key) == value, 'asset manifest agrees with exact framing: ' + key)
    for key in ('loaded_span_bytes', 'loaded_span_sha256', 'segment_record_count',
                'nonzero_segment_count', 'segments', 'entry_offset', 'vector_offset',
                'low_vector_offset', 'runtime_vector_offset', 'boot_pt_offset'):
        require(document.get(key) == geometry[key], 'asset manifest agrees with independent Mach-O: ' + key)
    require(document.get('header_sha256') == sha(header), 'actual compiled expected inner header')
    require(document.get('scratch_bytes') == 13376 and document.get('codec_roundtrip') is True,
            'standard decoder scratch size and producer roundtrip declaration')
    canonical = dict(schema='thor-fullseq01-validated-full-kernel-v1',
                     manifest_path=str(manifest_path.relative_to(root)), manifest_sha256=hash_file(manifest_path),
                     raw_path=str(paths['raw'].relative_to(root)), compressed_path=str(paths['compressed'].relative_to(root)),
                     header_path=str(paths['header'].relative_to(root)), header_sha256=sha(header), **inner,
                     loaded_span_sha256=geometry['loaded_span_sha256'],
                     segment_record_count=geometry['segment_record_count'], scratch_bytes=13376,
                     entry_offset=geometry['entry_offset'], vector_offset=geometry['vector_offset'],
                     low_vector_offset=geometry['low_vector_offset'],
                     runtime_vector_offset=geometry['runtime_vector_offset'], boot_pt_offset=geometry['boot_pt_offset'],
                     full_kernel_entry_executed=False)
    return canonical, geometry, paths


class CodeView:
    """Independent nlist/section reader; address aliases share a unique bound."""
    def __init__(self, raw):
        self.raw = raw
        self.geometry = parse_macho(raw)
        self.sections, self.symbols, self.uuid_ranges = [], {}, []
        at = 32
        for _ in range(struct.unpack_from('<I', raw, 16)[0]):
            command, size = struct.unpack_from('<II', raw, at)
            if command == 0x19:
                for i in range(struct.unpack_from('<I', raw, at + 64)[0]):
                    start, length, offset = struct.unpack_from('<QQI', raw, at + 72 + 80 * i + 32)
                    self.sections.append((start, length, offset))
            elif command == 2:
                symoff, count, stroff, strings = struct.unpack_from('<4I', raw, at + 8)
            elif command == 0x1b:
                require(size == 24, 'LC_UUID has exactly one sixteen-byte payload')
                self.uuid_ranges.append((at + 8, 16))
            at += size
        for i in range(count):
            index, kind, _, _, value = struct.unpack_from('<IBBHQ', raw, symoff + 16 * i)
            if kind & 0xe0 or kind & 0x0e != 0x0e:
                continue
            require(index < strings, 'code symbol string index')
            end = raw.find(b'\0', stroff + index, stroff + strings)
            require(end >= 0, 'code symbol name terminates')
            name = raw[stroff + index:end].decode()
            self.symbols.setdefault(name, []).append(value)
        self.names_at = {}
        for name, values in self.symbols.items():
            for value in values:
                self.names_at.setdefault(value, []).append(name)

    def address(self, name):
        values = self.symbols.get('_' + name, [])
        require(len(values) == 1, 'one native function anchor: ' + name)
        return values[0]

    def read(self, address, length):
        matches = [(start, size, offset) for start, size, offset in self.sections
                   if start <= address and length <= size and address - start <= size - length
                   and offset + address - start + length <= len(self.raw)]
        require(len(matches) == 1, 'one actual file-backed code interval: ' + hex(address))
        start, _, offset = matches[0]
        return self.raw[offset + address - start:offset + address - start + length]

    def body(self, name):
        start = self.address(name)
        sections = [(a, n) for a, n, _ in self.sections if a <= start < a + n]
        require(len(sections) == 1, 'function belongs to one section')
        a, n = sections[0]
        stop = min([v for v in self.names_at if start < v < a + n] or [a + n])
        require(stop > start and (stop - start) % 4 == 0, 'aligned unique-address function extent')
        return start, self.read(start, stop - start)

    def calls(self, name):
        start, data = self.body(name)
        rows = []
        for offset in range(0, len(data), 4):
            word = struct.unpack_from('<I', data, offset)[0]
            if word & 0xfc000000 not in (0x14000000, 0x94000000):
                continue
            immediate = word & 0x3ffffff
            if immediate & (1 << 25):
                immediate -= 1 << 26
            target = start + offset + immediate * 4
            if target in self.names_at:
                rows.append(dict(pc=hex(start + offset), word=hex(word),
                                 kind='BL' if word >> 31 else 'B', target=hex(target),
                                 symbols=self.names_at[target]))
        return rows

    def normalized_uuid(self):
        require(len(self.uuid_ranges) == 1, 'exactly one real LC_UUID, no arbitrary excluded bytes')
        data = bytearray(self.raw)
        for at, length in self.uuid_ranges:
            data[at:at + length] = bytes(length)
        return bytes(data)


def freeze_sources(checks, mapping, base=None, original_base=None):
    checks.require(isinstance(mapping, dict) and bool(mapping), 'nonempty actual source closure')
    for name, expected in mapping.items():
        checks.require(isinstance(expected, str) and len(expected) == 64, 'source digest is SHA256')
        path = Path(name)
        if not path.is_absolute() and base is not None:
            path = Path(base) / path
        elif path.is_absolute() and original_base is not None and path.is_relative_to(original_base):
            path = Path(base) / path.relative_to(original_base)
        checks.freeze(path, expected)


def validate_native(checks, candidate, xsrc, geometry):
    candidate, xsrc = rooted(checks.root, candidate), rooted(checks.root, xsrc)
    aggregate, _ = checks.document(candidate / 'native-build.json')
    checks.require(aggregate['schema'] == 'thor-fullseq01-execution-kernel-build-v1'
                   and aggregate['result'] == 'PASS_FULLSEQ01_EXECUTION_KERNEL_STATIC_CONTENT',
                   'formal merged execution-kernel build identity')
    checks.require(aggregate.get('phone_actions') == 0 and aggregate.get('hardware_tested') is False
                   and aggregate.get('full_kernel_entry_executed') is False
                   and aggregate.get('foreign_kernel_abi_qualified') is False
                   and aggregate.get('runtime_qualified') is False,
                   'host static proof retains hardware, foreign ABI and runtime frontier')
    kernel = checks.freeze(aggregate['kernel_path'], aggregate['kernel_sha256'], aggregate['kernel_bytes'])
    checks.require(geometry['raw_sha256'] == aggregate['kernel_sha256'], 'full execution asset is the merged candidate')
    for name, value in aggregate['geometry'].items():
        checks.require(geometry[name] == value, 'dynamic full kernel geometry: ' + name)
    proof, _ = checks.document(aggregate['native_selfbuild_proof_path'], aggregate['native_selfbuild_proof_sha256'])
    checks.require(proof['schema'] == 'thor-full-seq01-native-build-v1'
                   and proof['result'] == 'PASS_HOST_DARWIN22_NATIVE_SEQUENCE'
                   and proof['boot_args_bytes'] == 736
                   and proof['baseline_commit'] == 'b39559d9cbb56db43dcbf49d8ef08a7bcccb1abc',
                   'self-built Darwin22 baseline and ABI, separate from the merged execution kernel')
    native = checks.freeze(aggregate['native_kernel_path'], proof['kernel_sha256'], proof['kernel_bytes'])
    checks.freeze(proof['kernel']['path'], proof['kernel_sha256'], proof['kernel_bytes'])
    checks.require(aggregate['native_kernel_sha256'] == proof['kernel_sha256']
                   and aggregate['source_commit'] == proof['source_commit'] == proof['build_source_commit'],
                   'native input source revision and actual stripped bytes are the declared build')
    head = subprocess.check_output(['git', '-C', str(xsrc), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(xsrc), 'status', '--porcelain'], text=True)
    checks.require(head == proof['source_commit'] and not dirty, 'native production tree is exact clean formal HEAD')
    freeze_sources(checks, proof['source_files_sha256'])
    freeze_sources(checks, aggregate['source_files_sha256'])
    freeze_sources(checks, proof['prepared_source_sha256'], xsrc / '.cache/xnu-work')
    expected_stages = [dict(index=i, label=label, initial='BLOCKED') for i, label in enumerate(STAGES)]
    checks.require(proof['stage_contract'] == aggregate['stage_contract'] == expected_stages,
                   'sixteen stages initialize BLOCKED and 0F denotes USER_EXEC')
    checks.require(proof['readonly_storage'] is True and proof['secondary_cpus_gated'] is True
                   and proof['panic_first_failure_latched'] is True and proof['data_template_phone_present'] is False,
                   'read-only storage, secondary CPU and failure gates retain unproven root backing')
    view, full = CodeView(native.read_bytes()), CodeView(kernel.read_bytes())
    for field, key in [('entry_offset', 'entry_offset'), ('vector_offset', 'vector_offset'),
                       ('low_vector_offset', 'low_vector_offset'), ('runtime_vector_offset', 'runtime_vector_offset'),
                       ('bootstrap_pt_offset', 'boot_pt_offset'), ('loaded_span', 'loaded_span_bytes')]:
        checks.require(proof['geometry'][field] == view.geometry[key], 'actual native Mach-O identity: ' + field)
    checks.require(set(proof['raw_linked_audit']) == set(NATURAL_CALLS), 'complete fixed natural native audit function set')
    for name, expected in NATURAL_CALLS.items():
        address, data = view.body(name)
        declared = proof['raw_linked_audit'][name]
        checks.require((hex(address), len(data), sha(data)) ==
                       (declared['address'], declared['bytes'], declared['sha256']),
                       'independent native function unique-address extent and raw bytes: ' + name)
        calls = view.calls(name)
        checks.require(calls == declared['calls'], 'complete independently decoded named B/BL inventory: ' + name)
        checks.require(full.read(address, len(data)) == data, 'merged executable preserves native function bytes: ' + name)
        cursor = 0
        for row in calls:
            if cursor < len(expected) and '_' + expected[cursor] in row['symbols']:
                cursor += 1
        checks.require(cursor == len(expected), 'actual natural call subsequence, no stage-number timing inference: ' + name)
    low, handler = view.address('LowExceptionVectorBase'), view.address('thor_full_early_fault')
    for index in range(16):
        pc = low + 128 * index
        word = struct.unpack('<I', full.read(pc, 4))[0]
        immediate = word & 0x3ffffff
        if immediate & (1 << 25):
            immediate -= 1 << 26
        checks.require(word & 0xfc000000 == 0x14000000 and pc + 4 * immediate == handler,
                       'actual low-vector entry branches to terminal first-fault recorder: %02X' % index)
    old = checks.freeze(checks.root / 'runs/thor-20261027-ios-full-seq01/l2/native/native-link11.macho',
                        proof['reproducibility']['link11_sha256'])
    previous = CodeView(old.read_bytes())
    normalized = view.normalized_uuid()
    checks.require(previous.uuid_ranges == view.uuid_ranges
                   and previous.normalized_uuid() == normalized
                   and sha(normalized) == proof['reproducibility']['normalized_sha256'],
                   'two real native links differ only in the actual LC_UUID sixteen-byte field')
    checks.require(proof['reproducibility']['raw_byte_identical'] == (previous.raw == view.raw)
                   and proof['reproducibility']['uuid_ranges'] == [list(row) for row in view.uuid_ranges],
                   'double-link report states actual raw inequality and exact normalization scope')
    for record in proof['host_proofs'].values():
        checks.freeze(record['path'], record['sha256'])
    fault, _ = checks.document(checks.root / 'runs/thor-20261027-ios-full-seq01/l2/native/early-fault-machinecode.json',
                                'e488193d5e8f121039b1fc0ef1108d41571ec78474d2f634cb6c7bdc3076fae9')
    freeze_sources(checks, fault['source_sha256'])
    handler_row = fault['handler']
    checks.require(fault['result'] == 'PASS_FROZEN_RAW_FIRST_FAILURE_LATCH'
                   and fault['native_kernel_sha256'] == proof['kernel_sha256']
                   and fault['case_count'] == len(fault['cases']) == 176
                   and len(fault['negative_guard_mutations']) == 6
                   and fault['phone_actions'] == 0 and fault['hardware_execution_claimed'] is False,
                   'bound independent first-fault raw instruction and six behavioral guard-removal regressions')
    body = full.read(int(handler_row['entry'], 16), handler_row['bytes'])
    checks.require(int(handler_row['entry'], 16) == handler and handler_row['instructions'] == len(body) // 4 == 57
                   and sha(body) == handler_row['raw_sha256'] and body.hex() == handler_row['raw_hex'],
                   'actual merged-kernel first-fault handler exactly matches independently executed 57 instruction words')
    bss, length = int(handler_row['BSS'], 16), handler_row['BSS_bytes']
    checks.require(length == 64 and bss == view.address('gThorFullEarlyFault')
                   and any(row['virtual_address'] + row['file_bytes'] <= bss
                           and bss + length <= row['virtual_address'] + row['virtual_size']
                           for row in view.geometry['segments']),
                   'first-fault receipt occupies the actual owned zero-filled 64-byte native BSS extent')
    return aggregate, proof


CODEC_HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "BaseUefiDecompressLibInternals.h"
static int intact(const unsigned char *p,size_t n){for(size_t i=0;i<n;i++)if(p[i]!=0xa5)return 0;return 1;}
int main(int argc,char **argv){
 if(argc!=4)return 2; unsigned long expected=strtoul(argv[3],0,10);
 FILE *f=fopen(argv[1],"rb");if(!f)return 3;
 if(fseek(f,0,SEEK_END))return 4;long z=ftell(f);
 if(z<8||z>20*1024*1024)return 5;rewind(f);
 unsigned char *input=malloc((size_t)z);if(!input||fread(input,1,(size_t)z,f)!=(size_t)z)return 6;fclose(f);
 UINT32 bytes=0,scratch=0;
 if(UefiDecompressGetInfo(input,(UINT32)z,&bytes,&scratch)!=0||bytes!=expected||scratch!=sizeof(SCRATCH_DATA)||scratch!=13376U)return 7;
 unsigned char *out=malloc((size_t)bytes+64),*work=malloc((size_t)scratch+64);if(!out||!work)return 8;
 memset(out,0xa5,(size_t)bytes+64);memset(work,0xa5,(size_t)scratch+64);
 if(UefiDecompress(input,out+32,work+32)!=0)return 9;
 if(!intact(out,32)||!intact(out+32+bytes,32)||!intact(work,32)||!intact(work+32+scratch,32))return 10;
 f=fopen(argv[2],"wb");if(!f||fwrite(out+32,1,bytes,f)!=bytes||fclose(f))return 11;
 printf("{\"decoded_raw_bytes\":%u,\"scratch_bytes\":%u,\"output_canaries_intact\":true,\"scratch_canaries_intact\":true}\n",bytes,scratch);
 free(input);free(out);free(work);return 0;
}
'''


def fresh_codec(checks, asset, paths, output):
    directory = checks.root / 'evidence/build/vm-mem08/host-codec/source'
    checks.require(directory.is_dir(), 'fixed original Mu standard production codec closure is present')
    for path in sorted(directory.rglob('*')):
        if path.is_file():
            checks.freeze(path)
    codec = checks.freeze(directory / 'BaseUefiDecompressLib.c',
                          '1eb6bcddb9ab4bafb1a0ed97ab84b5b1038350062ce883ac22876ebccf2c0bce')
    checks.freeze(directory / 'BaseUefiDecompressLibInternals.h',
                  '78e680ad56be7fd9bf6dac077d543c7989212a74d4ade6ce92b6d46961b040c2')
    with tempfile.TemporaryDirectory(prefix='fullseq-codec-', dir=output) as temporary:
        folder = Path(temporary)
        harness, executable, decoded = folder / 'decode.c', folder / 'decode', folder / 'decoded.macho'
        harness.write_text(CODEC_HARNESS)
        run_command(checks, ['xcrun', 'clang', '-std=c11', '-O2', '-I', directory, harness, codec, '-o', executable],
                    output / 'codec-compile.log')
        run_command(checks, [executable, paths['compressed'], decoded, str(asset['raw_bytes'])], output / 'codec-execute.log')
        checks.require(decoded.stat().st_size == asset['raw_bytes'] and hash_file(decoded) == asset['raw_sha256'],
                       'fresh standard production decoder yields exact new full merged kernel bytes')
        actual = parse_macho(decoded.read_bytes())
        checks.require(actual['loaded_span_sha256'] == asset['loaded_span_sha256'],
                       'fresh decoded Mach-O reconstructs actual new full loaded-span SHA')
    return dict(result='PASS_REAL_STANDARD_PRODUCTION_DECODE', decoded_raw_sha256=asset['raw_sha256'],
                loaded_span_sha256=asset['loaded_span_sha256'], scratch_bytes=13376,
                output_canaries_intact=True, scratch_canaries_intact=True, harness_sha256=sha(CODEC_HARNESS.encode()),
                phone_actions=0)


def fresh_native_guards(checks, xsrc, kernel, native, output):
    xsrc = rooted(checks.root, xsrc)
    python = sys.executable
    tests = xsrc / 'tests'
    for name in ('test_thor_full_progress.py', 'test_thor_full_chain.py',
                 'test_thor_fullseq_vm_guards.py', 'test_thor_full_storage_readonly.py',
                 'test_thor_full_bootstrap.py'):
        checks.freeze(tests / name)
    jobs = [
        ('progress', [python, '-B', tests / 'test_thor_full_progress.py', '--proof', output / 'progress.json'], {}),
        ('vm', [python, '-B', tests / 'test_thor_fullseq_vm_guards.py'],
         {'THOR_FULLSEQ_VM_GUARD_PROOF': str(output / 'vm.json')}),
        ('storage', [python, '-B', tests / 'test_thor_full_storage_readonly.py'],
         {'THOR_STORAGE_READONLY_PROOF': str(output / 'storage.json')}),
        ('bootstrap-tests', [python, '-B', tests / 'test_thor_full_bootstrap.py', '--proof', output / 'bootstrap-tests.json'], {}),
    ]
    for name, command, additions in jobs:
        environment = dict(os.environ)
        environment.update(additions)
        run_command(checks, command, output / (name + '.log'), cwd=xsrc, env=environment)
    chain_script = tests / 'test_thor_full_chain.py'
    chain = ("import importlib.util;from pathlib import Path;"
             f"s=importlib.util.spec_from_file_location('fresh_chain',{str(chain_script)!r});"
             "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
             f"m.RUN=Path({str(output)!r});m.main()")
    run_command(checks, [python, '-B', '-c', chain], output / 'chain.log', cwd=xsrc)
    checks.require(not (xsrc / 'tests/native-chain-host.json').exists(), 'chain replay redirected only its output directory')
    bootstrap = xsrc / 'scripts/audit-thor-full-bootstrap.py'
    checks.freeze(bootstrap)
    start_object = checks.freeze(xsrc / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/osfmk/DEVELOPMENT/start.o')
    work = output / 'bootstrap-work'
    work.mkdir()
    run_command(checks, [python, '-B', bootstrap, '--source-root', xsrc, '--image', kernel,
                         '--object', start_object, '--workdir', work, '--proof', output / 'bootstrap.json'],
                output / 'bootstrap.log', cwd=xsrc)
    progress, _ = checks.document(output / 'progress.json')
    checks.require(progress['result'] == 'PASS_REAL_PROGRESS_BACKEND' and progress['tests_run'] == 22
                   and progress['failures'] == progress['errors'] == 0
                   and progress['executed_guard_removal_mutants_rejected'] == 7
                   and progress['runtime_hardware_claimed'] is False,
                   'fresh production progress writer and seven executed guard-removal mutants')
    vm, _ = checks.document(output / 'vm.json')
    checks.require(vm['result'] == 'PASS_ACTUAL_C_VM_GUARDS_WITH_FAULTS'
                   and (vm['span_cases'], vm['allocator_cases'], vm['metadata_cases'], vm['next_page_cases'],
                        vm['guard_removal_rejections']) == (4103, 7, 12, 9, 5),
                   'fresh actual bank/allocator/metadata/next-page guards reject all existing fault cases')
    checks.require(len(vm['native_source_preservation']) == 3
                   and all(row['native_lines_preserved'] for row in vm['native_source_preservation'].values()),
                   'three native VM algorithms remain exact after transparent observer stripping')
    storage, _ = checks.document(output / 'storage.json')
    checks.require(storage['result'] == 'PASS_ACTUAL_COMMAND_READONLY_GUARDS'
                   and storage['source_unchanged_during_execution'] is True
                   and (storage['test_cases_total'], storage['guard_removal_rejections'],
                        storage['smmu_guard_mutation_rejections']) == (2675, 5, 2),
                   'fresh actual storage/DMA/SMMU guards and seven real mutants reject destructive paths')
    chain_proof, _ = checks.document(output / 'native-chain-host.json')
    checks.require(chain_proof['result'] == 'PASS_BANK_AWARE_PRE_VM_AND_POST_VM_GUARDS'
                   and chain_proof['checks'] == 4138 and chain_proof['bank_property_unaligned'] is True,
                   'fresh actual pre/post VM and DMA-span bank-aware production guards')
    tested, _ = checks.document(output / 'bootstrap-tests.json')
    checks.require(tested['result'] == 'PASS_RAW_BOOTSTRAP_AND_MUTATIONS'
                   and tested['failures'] == tested['errors'] == 0
                   and tested['native_guard_fault_cases'] == 33
                   and tested['grant_cases'] == 4 and tested['opcode_mutation_classes'] == 7,
                   'fresh actual A64 bootstrap fault and opcode-mutation suite')
    machine, _ = checks.document(output / 'bootstrap.json')
    checks.require(machine['result'] == 'PASS_EXACT_OWNED_BOOTSTRAP'
                   and machine['image_sha256'] == hash_file(kernel)
                   and machine['native_guard_rejections_count'] == 33
                   and len(machine['cases']) == 4 and machine['runtime_hardware_claimed'] is False,
                   'fresh actual merged-kernel A64 bootstrap execution, four owned grants, no CPU claim')
    full = CodeView(Path(kernel).read_bytes())
    for name, row in machine['raw_blocks'].items():
        begin, end = int(row['begin'], 16), int(row['end'], 16)
        checks.require(begin == full.address('thor_full_' + name + '_begin')
                       and end == full.address('thor_full_' + name + '_end')
                       and end - begin == row['bytes']
                       and sha(full.read(begin, end - begin)) == row['sha256'],
                       'independent raw bootstrap anchor and bytes: ' + name)
    for row in machine['cases']:
        checks.require(row['GOP_pages'] == 391 and row['no_unknown_RAM_or_neighbor_pages'] is True
                       and row['owned_pages_each_root'] * 4096 == row['grant_bytes'],
                       'actual bootstrap grants only owned memory and 391 framebuffer pages')
    for proof in (progress, vm, storage, chain_proof, tested, machine):
        checks.require(proof.get('phone_actions') == 0, 'host guard replay issued zero phone actions')
        if 'source_sha256' in proof:
            freeze_sources(checks, proof['source_sha256'], xsrc)
    freeze_sources(checks, chain_proof['production_source_sha256'], xsrc / 'drivers/ThorFullSeq')
    return dict(progress=progress, vm=vm, storage=storage, chain=chain_proof,
                bootstrap_tests=tested, bootstrap=machine)


def fresh_prelink(checks, candidate, output):
    candidate = rooted(checks.root, candidate)
    document, _ = checks.document(candidate / 'native-build.json')
    declared, _ = checks.document(document['prelink_integrity_path'], document['prelink_integrity_sha256'])
    checks.freeze(document['prelink_emission_path'], document['prelink_emission_sha256'])
    auditor = checks.freeze(Path(__file__).with_name('fullseq_prelink_audit.py'), declared['auditor_sha256'])
    original = checks.freeze(declared['inputs']['original'], document['original_kernel_sha256'])
    run_command(checks, [sys.executable, '-B', auditor, '--native', candidate / 'native.macho',
                         '--original', original, '--merged', candidate / 'kernel.macho',
                         '--emission', rooted(checks.root, document['prelink_emission_path']),
                         '--output', output / 'prelink.json'], output / 'prelink.log')
    actual, _ = checks.document(output / 'prelink.json')
    checks.require(actual['result'] == 'PASS_STATIC_PRELINK_CONTENT_AND_DIRECT_PCREL'
                   and actual['merged_sha256'] == document['kernel_sha256']
                   and actual['native_sha256'] == document['native_kernel_sha256']
                   and actual['component_count'] == 11
                   and actual['pointer_counts'] == {'component': 5742, 'kernel': 7230}
                   and actual['all_coalesced_bytes_reconstructed'] is True
                   and actual['all_pointer_sites_reconstructed'] is True
                   and actual['native_payload_unchanged'] is True
                   and actual['metadata_and_static_top_verified'] is True,
                   'fresh independent full prelink reconstructs all native, foreign, pointer and direct PC-relative bytes')
    checks.require(actual['phone_actions'] == 0 and actual['foreign_kernel_abi_qualified'] is False
                   and actual['runtime_qualified'] is False and actual['boot_eligible'] is False
                   and actual['register_dataflow_qualified'] is False
                   and all(value is False for value in actual['preserved_emitter_unqualified_flags'].values()),
                   'foreign ABI, complete register flow and runtime remain explicitly unqualified experimental frontier')
    return actual


def parse_adt(data):
    nodes, offsets, cursor = {}, {}, 0

    def node(parent, depth):
        nonlocal cursor
        require(depth <= 32 and len(nodes) < 512 and cursor + 8 <= len(data), 'bounded complete ADT node')
        count, children = struct.unpack_from('<II', data, cursor)
        cursor += 8
        require(count <= 256 and children <= 256, 'bounded ADT property and child counts')
        properties, locations = {}, {}
        for _ in range(count):
            require(cursor + 36 <= len(data), 'complete ADT property prefix')
            key = data[cursor:cursor + 32]
            require(b'\0' in key, 'terminated ADT property key')
            key = key.split(b'\0', 1)[0].decode('ascii')
            size = struct.unpack_from('<I', data, cursor + 32)[0]
            require(key and key not in properties and size <= 1 << 20, 'unique bounded ADT property')
            cursor += 36
            require(size <= len(data) - cursor, 'complete ADT property value')
            properties[key], locations[key] = data[cursor:cursor + size], (cursor, size)
            cursor += (size + 3) & ~3
        require('name' in properties and properties['name'].endswith(b'\0'), 'named ADT node')
        name = properties['name'][:-1].decode('ascii')
        require(name and '/' not in name and '\0' not in name, 'simple complete ADT node name')
        path = '/' if parent is None else parent.rstrip('/') + '/' + name
        require(path not in nodes, 'unique ADT node path')
        nodes[path], offsets[path] = properties, locations
        for _ in range(children):
            node(path, depth + 1)

    node(None, 0)
    require(cursor == len(data), 'ADT consumes exact canonical bytes without trailing nodes or garbage')
    return nodes, offsets


def fresh_adt(checks, uefi, output):
    base = checks.root / 'evidence/build/ios-full-seq01/adt'
    formal, _ = checks.document(base / 'production-adt-proof-04.json',
                                '1dd836367075b256cd69925fdb0e28cdc0d03be6e5fd4de30e3853643d98a370')
    mirror = base / 'production-source'
    freeze_sources(checks, formal['source_files_sha256'], mirror)
    if uefi is not None:
        freeze_sources(checks, formal['source_files_sha256'], uefi)
    checks.require(formal['result'] == 'PASS_PRODUCTION_ADT_FDT_BANKS_AND_FUNCTIONAL_SOURCE_QUALIFICATION'
                   and formal['test_count'] == 62 and formal['canonical_adt_bytes'] == 15728
                   and formal['slot_bytes'] == 16384 and formal['phone_actions'] == 0,
                   'final OS-root consumer ADT and functional UFS source-qualification revision')
    checks.require(formal['holes_preserved_in_bank_table'] is True
                   and formal['cpu_registers_immutable'] is True and formal['only_boot_cpu_state_changed'] is True
                   and formal['physical_ddr_capacity_claimed'] is False and formal['new_memory_grant_pages'] == 0
                   and formal['storage_compatible_unmatched_and_disabled'] is False
                   and formal['same_snapshot_identity_required'] is True
                   and formal['source_qualification_is_hardware_success'] is False
                   and formal['native_reads_source_qualified'] is False
                   and formal['native_readback_pending'] is True,
                   'real bank holes, boot CPU and source qualification keep their hardware boundaries')
    suite = checks.freeze(mirror / formal['test_suite'])
    env = dict(os.environ, THOR_FULLSEQ_ADT_PROJECT_ROOT=str(checks.root),
               THOR_FULLSEQ_ADT_AUDIT=str(output / 'adt.json'))
    run_command(checks, [sys.executable, '-B', suite], output / 'adt.log', env=env)
    actual, _ = checks.document(output / 'adt.json')
    freeze_sources(checks, actual['source_files_sha256'], mirror)
    checks.require(actual['schema'] == 'thor-fullseq01-production-adt-and-fdt-host-v1'
                   and actual['phone_actions'] == 0 and actual['adt_bytes'] == formal['canonical_adt_bytes']
                   and actual['actual_thor_fdt_sha256'] == formal['source_fdt_sha256']
                   and len(actual['results']) >= 95,
                   'fresh real production ADT/FDT/hardware parser suite executes final template and fault traces')
    header = mirror / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorFullSeqAdtData.h'
    text = header.read_text()
    array = re.search(r'mThorFullSeqAdtTemplate\[\]\s*=\s*\{([^}]+)\}', text, re.S)
    checks.require(array is not None, 'one real compiled canonical ADT byte array')
    data = bytes(int(value, 16) for value in re.findall(r'0x([0-9a-fA-F]{2})\b', array[1]))
    checks.require(len(data) == formal['canonical_adt_bytes'] and sha(data) == formal['canonical_adt_sha256'],
                   'independently extract actual compiled template bytes and canonical SHA')
    nodes, offsets = parse_adt(data)
    checks.require(nodes['/chosen']['boot-uuid'] == b'C5DAF029-2070-4F68-B3CC-1D1320344CF0\0'
                   and 'root-matching' not in nodes['/chosen']
                   and 'RAMDisk' not in nodes['/chosen/memory-map'],
                   'actual OS UUID consumer property, no fabricated root override or RAMDisk')
    checks.require('system-volume-auth-blob' not in nodes['/chosen']
                   and nodes['/chosen/memory-map']['TrustCache'] == bytes(16),
                   'auth blob has no native consumer claim and TC runtime tuple starts zero')
    for field in formal['dynamic_fields']:
        checks.require(offsets[field['path']][field['property']] == (field['offset'], field['bytes'])
                       and nodes[field['path']][field['property']] == bytes(field['bytes']),
                       'independent canonical runtime ADT field bounds and zero values: ' + field['property'])
    for path in ('/cpus', '/arm-io', '/defaults', '/options', '/arm-io/ufshc'):
        checks.require(path in nodes, 'actual required platform ADT node: ' + path)
    freeze_sources(checks, formal['upstream_resource_evidence_sha256'])
    return dict(formal=formal, fresh=actual, parsed_node_count=len(nodes),
                canonical_adt_sha256=sha(data), phone_actions=0)


def validate_system_inputs(checks, native):
    path = checks.root / 'evidence/build/ios-full-seq01/assets/host-prepared/prepared-system-inputs.json'
    document, _ = checks.document(path, 'c5ee26e7099ff0a67daaeb98971d565049ade50dd30e4202aaa76cd6a963561a')
    checks.freeze(document['producer_path'], document['producer_sha256'])
    checks.freeze(document['fetched_report'], document['fetched_report_sha256'])
    checks.require(document['result'] == 'PASS_HOST_ASSETS_PREPARED_THOR_RUNTIME_ROOT_BLOCKED'
                   and document['identity']['version'] == '16.6.1' and document['identity']['build'] == '20G81'
                   and document['official_images_readable_on_host'] is True
                   and document['thor_root_media_qualified'] is False and document['runtime_mount_proven'] is False
                   and document['ios_home_screen_proven'] is False and document['phone_actions'] == 0,
                   'real official host OS/media inventory remains distinct from unproven phone backing and GUI')
    for name in ('root_volume_uuid', 'systemos_volume_uuid'):
        checks.require(native[name] == document[name], 'native and real official media UUID: ' + name)
    checks.require(native['data_template_uuid'] == document['data_volume_uuid']
                   and document['data_template_empty'] is True
                   and document['data_ios16_compatibility_qualified'] is False,
                   'empty case-sensitive host Data template has no runtime compatibility claim')
    derived = document['loader_inputs']
    bundle = derived['loader_trustcache_bundle']
    raw = checks.freeze(bundle['path'], bundle['sha256'], bundle['bytes']).read_bytes()
    checks.require(struct.unpack_from('<4I', raw) == (3, 16, 60280, 61768),
                   'actual three-cache TrustCache offset-table ABI')
    offsets = [16, 60280, 61768, len(raw)]
    for index, record in enumerate(derived['trustcaches']):
        part = checks.freeze(record['path'], record['sha256'], record['bytes']).read_bytes()
        checks.freeze(record['source_path'], record['source_sha256'])
        checks.require(raw[offsets[index]:offsets[index + 1]] == part
                       and struct.unpack_from('<I', part)[0] == 2
                       and struct.unpack_from('<I', part, 20)[0] == record['entries']
                       and len(part) == 24 + 24 * record['entries'],
                       'actual official v2 TrustCache bytes/count/offset: ' + record['component'])
    auth = derived['system_volume_auth_blob']
    checks.freeze(auth['path'], auth['sha256'], auth['bytes'])
    checks.freeze(auth['source_path'], auth['source_sha256'])
    checks.require(derived['signature_verification_performed'] is False, 'source inventory does not assert IMG4 signature verification')
    return dict(result=document['result'], root_volume_uuid=document['root_volume_uuid'],
                systemos_volume_uuid=document['systemos_volume_uuid'], data_volume_uuid=document['data_volume_uuid'],
                loader_trustcache_sha256=bundle['sha256'], trustcache_bytes=len(raw),
                trustcache_status='TC_INPUT_STAGED_RUNTIME_NOT_QUALIFIED', trustcache_runtime_qualified=False,
                auth_available_not_consumed=True, actual_phone_root_backing_qualified=False, phone_actions=0)


def validate_source_preservation(checks, xsrc, output):
    directory = checks.root / 'runs/thor-20261027-ios-full-seq01/l2/native'
    proof, _ = checks.document(directory / 'source-preservation.json',
                              '4b0f7c4628ef3a2fd89d7b39ba012a911e40a0cc685101b2d17eea05fece5f5d')
    closure, _ = checks.document(directory / 'source-preservation-files.json',
                                'b8011e478e82a13c6a83cd4c7567f5af1e38e56f1b83217d428da8ef1ff6f86b')
    checks.freeze(directory / 'produce-source-preservation.py', closure['producer_sha256'])
    checks.require(proof['result'] == 'PASS_EXACT_F2_BASELINE_PLUS_DECLARED_OBSERVERS'
                   and proof['baseline_tools_commit'] == 'b39559d9cbb56db43dcbf49d8ef08a7bcccb1abc'
                   and proof['unlogged_source_drift'] == [] and proof['reconstructed_changed_TUs_exact'] is True
                   and proof['phone_actions'] == 0, 'fixed F2 native source preservation identity')
    inventories = {}
    roots = {}
    for role in ('baseline', 'current'):
        record = closure[role]
        inventory, _ = checks.document(record['path'], record['sha256'])
        roots[role] = rooted(checks.root, record['root'])
        checks.require(len(inventory) == proof[role + '_source_files'], 'exact ' + role + ' source inventory count')
        actual = {}
        for path in roots[role].rglob('*'):
            relative = path.relative_to(roots[role])
            if not path.is_file() or '.git' in relative.parts or not (
                    path.suffix in ('.c', '.cpp', '.h', '.hpp', '.s', '.S') or path.name in ('Makefile', 'files')):
                continue
            before = path.stat()
            digest = hash_file(path)
            after = path.stat()
            require((before.st_ino, before.st_size, before.st_mtime_ns) ==
                    (after.st_ino, after.st_size, after.st_mtime_ns), 'source inventory file changed during independent hash')
            require(path.resolve().is_relative_to(checks.root), 'source inventory resolved path stays inside project')
            actual[str(relative)] = digest
            value = dict(bytes=before.st_size, sha256=digest)
            old = checks.inputs.get(str(path.resolve()))
            require(old is None or old == value, 'source closure conflicts with prepared compilation input')
            checks.inputs[str(path.resolve())] = value
        checks.require(actual == inventory, 'independent complete current ' + role + ' native source tree matches frozen inventory')
        inventories[role] = actual
    baseline, current = inventories['baseline'], inventories['current']
    changed = sorted(name for name in baseline if current.get(name) != baseline[name])
    extra = sorted(set(current) - set(baseline))
    checks.require(changed == proof['changed_files'] and extra == proof['extra_files']
                   and len(baseline) - len(changed) == proof['unchanged_files'] == 3834,
                   'all 3834 unchanged F2 native files plus exactly twelve transparent changes and six additions')
    patch = checks.freeze(closure['patch']['path'], proof['patch_sha256'])
    with tempfile.TemporaryDirectory(prefix='fullseq-source-rebuild-', dir=output) as temp:
        temporary = Path(temp)
        for name in changed:
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((roots['baseline'] / name).read_bytes())
        run_command(checks, ['git', '-C', temporary, 'apply', patch], output / 'native-source-reconstruct.log')
        checks.require(all(hash_file(temporary / name) == current[name] for name in changed),
                       'independent git-apply reconstruction of all twelve prepared native translation units')
    checks.require(roots['current'] == rooted(checks.root, xsrc) / '.cache/xnu-work',
                   'rebuilt source inventory is the actual new compiled source tree')
    return dict(result=proof['result'], baseline_source_files=len(baseline),
                unchanged_files=3834, changed_files=changed, extra_files=extra,
                independently_reconstructed=True, phone_actions=0)


FD_RELATIVE = 'Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd'
BDS_RELATIVE = ('Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/'
                'ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi')


def validate_display(checks, xsrc, kernel, command_line):
    tokens = command_line.split()
    for key, value in (('pong_display', '1'), ('pong_gpu_only', '0')):
        checks.require([token for token in tokens if token.split('=', 1)[0] == key] == [key + '=' + value],
                       'one actual native display command-line token: ' + key)
    view = CodeView(Path(kernel).read_bytes())
    expected = {
        'bsd_autoconf': ['_native_pong_ui_services_start'],
        'native_pong_ui_services_start': ['__Z18MiniIOSurfaceStartP9IOService', '__Z28MiniIOMobileFramebufferStartP9IOService'],
    }
    graph = {}
    for name, targets in expected.items():
        calls = view.calls(name)
        cursor = 0
        for row in calls:
            if cursor < len(targets) and targets[cursor] in row['symbols']:
                cursor += 1
        checks.require(cursor == len(targets), 'actual native display provider call sequence: ' + name)
        address, data = view.body(name)
        graph[name] = dict(address=hex(address), bytes=len(data), sha256=sha(data), calls=calls)
    draw = '_Z' + 'N19IOMobileFramebuffer9drawLayerEP13MiniIOSurfacePKiS3_'
    address, data = view.body(draw)
    checks.require(len(data) > 64 and not any(name.startswith('_pong_scanout_') for name in view.symbols),
                   'actual CPU compositor remains linked under the inherited F2 GPU exclusion')
    source = rooted(checks.root, xsrc) / '.cache/xnu-work/iokit/Kernel'
    checks.freeze(source / 'MiniIOSurface.cpp')
    checks.freeze(source / 'MiniIOMobileFramebuffer.cpp')
    checks.freeze(rooted(checks.root, xsrc) / 'scripts/build-xnu-4k-baseline.sh')
    return dict(provider_graph=graph, cpu_draw_layer=dict(address=hex(address), bytes=len(data), sha256=sha(data)),
                kernel_command_line=command_line, cpu_compositor_linked=True, gpu_acceleration_qualified=False,
                actual_framebuffer_runtime_qualified=False, phone_actions=0)


def archive_member(data, wanted):
    require(data.startswith(b'!<arch>\n'), 'real native archive header')
    at, strings, found = 8, None, []
    while at < len(data):
        require(at + 60 <= len(data) and data[at + 58:at + 60] == b'`\n', 'complete archive member header')
        name = data[at:at + 16].decode('ascii').strip()
        try:
            size = int(data[at + 48:at + 58].decode('ascii').strip())
        except ValueError as exc:
            raise GateError('invalid native archive member size') from exc
        begin, end = at + 60, at + 60 + size
        require(size >= 0 and end <= len(data), 'bounded complete archive member bytes')
        payload = data[begin:end]
        if name == '//':
            require(strings is None, 'one GNU archive name table')
            strings = payload
        elif name.startswith('#1/'):
            count = int(name[3:])
            require(count <= len(payload), 'bounded BSD archive name')
            name, payload = payload[:count].rstrip(b'\0').decode(), payload[count:]
        elif name.startswith('/') and name[1:].isdigit():
            index = int(name[1:])
            require(strings is not None and index < len(strings), 'bounded GNU archive name offset')
            candidates = [value for value in (strings.find(b'\n', index), strings.find(b'\0', index)) if value >= 0]
            require(candidates, 'GNU/COFF archive name terminates')
            stop = min(candidates)
            name = strings[index:stop].rstrip(b'/\0').decode()
        else:
            name = name.rstrip('/')
        if Path(name).name == wanted:
            found.append(payload)
        at = end + (size & 1)
    require(at == len(data) and len(found) == 1, 'one exact actual native archive member: ' + wanted)
    return found[0]


def coff_symbols(data):
    require(len(data) >= 20, 'complete AArch64 COFF header')
    machine, sections = struct.unpack_from('<HH', data)
    symoff, count = struct.unpack_from('<II', data, 8)
    optional = struct.unpack_from('<H', data, 16)[0]
    require(machine == 0xaa64 and optional == 0 and 0 < sections <= 1024
            and 20 + 40 * sections <= len(data), 'ordinary bounded actual AArch64 COFF object')
    strings = symoff + 18 * count
    require(symoff >= 20 + sections * 40 and strings + 4 <= len(data), 'complete bounded COFF symbol table')
    string_bytes = struct.unpack_from('<I', data, strings)[0]
    require(4 <= string_bytes <= len(data) - strings, 'bounded COFF string table')
    result, i = [], 0
    while i < count:
        at = symoff + i * 18
        name = data[at:at + 8]
        if name[:4] == bytes(4):
            offset = struct.unpack_from('<I', name, 4)[0]
            require(4 <= offset < string_bytes, 'COFF string name stays in table')
            stop = data.find(b'\0', strings + offset, strings + string_bytes)
            require(stop >= 0, 'COFF long name terminates')
            name = data[strings + offset:stop]
        else:
            name = name.rstrip(b'\0')
        value, section = struct.unpack_from('<Ih', data, at + 8)
        aux = data[at + 17]
        require(i + aux < count and section <= sections, 'COFF auxiliary/section bounds')
        result.append((name.decode(), value, section))
        i += 1 + aux
    return result, sections


def parse_stack_report(data):
    result = {}
    for line in data.decode().splitlines():
        parts = line.split('\t')
        require(len(parts) == 3 and parts[2] == 'static', 'only measured static native compiler stack frames')
        name, value = parts[0].rsplit(':', 1)[-1], int(parts[1])
        require(name and name not in result and value >= 0, 'unique nonnegative actual native stack frame')
        result[name] = value
    require(result, 'nonempty actual compiler stack usage')
    return result


def pe_interval(data, address, count):
    require(data[:2] == b'MZ' and len(data) > 8192, 'complete linked PE DOS prefix')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    require(pe + 24 <= len(data) and data[pe:pe + 4] == b'PE\0\0', 'bounded actual PE signature')
    machine, nsections = struct.unpack_from('<HH', data, pe + 4)
    optional = struct.unpack_from('<H', data, pe + 20)[0]
    at = pe + 24
    require(machine == 0xaa64 and 2 <= nsections <= 12 and optional >= 160
            and at + optional + nsections * 40 <= len(data)
            and struct.unpack_from('<H', data, at)[0] == 0x20b,
            'complete real AArch64 PE32+ section table')
    sections = []
    for i in range(nsections):
        item = at + optional + 40 * i
        virtual_size, rva, size, offset = struct.unpack_from('<4I', data, item + 8)
        require(offset + size <= len(data), 'PE section raw bytes stay inside actual linked file')
        sections.append((data[item:item + 8].rstrip(b'\0'), rva, size, offset))

    def fetch(rva, length, executable=False):
        found = [(name, start, size, offset) for name, start, size, offset in sections
                 if start <= rva and rva + length <= start + size and (not executable or name == b'.text')]
        require(len(found) == 1, 'one complete actual PE file-backed interval')
        _, start, _, offset = found[0]
        return data[offset + rva - start:offset + rva - start + length]

    require(struct.unpack_from('<I', data, at + 108)[0] > 5, 'actual PE relocation directory is declared')
    reloc, size = struct.unpack_from('<II', data, at + 112 + 5 * 8)
    if size:
        raw = fetch(reloc, size)
        cursor = 0
        while cursor < size:
            require(cursor + 8 <= size, 'complete actual PE relocation block')
            page, length = struct.unpack_from('<II', raw, cursor)
            require(length >= 8 and length % 2 == 0 and length <= size - cursor, 'bounded PE relocation block')
            for index in range(cursor + 8, cursor + length, 2):
                word = struct.unpack_from('<H', raw, index)[0]
                require(not word >> 12 or not address <= page + (word & 4095) < address + count,
                        'no actual PE base relocation lies inside copied PIC interval')
            cursor += length
    return fetch(address, count, True)


def transition_immediates(raw):
    require(len(raw) == 528, 'new full-mode transition has actual fixed 528-byte interval')
    words = struct.unpack('<132I', raw)
    rows = []
    for i in range(len(words) - 3):
        first, second, add, compare = words[i:i + 4]
        register = first & 31
        if register not in (8, 9) or first & 0xffe00000 != 0xd2800000:
            continue
        if second & 0xffe0001f != 0xf2a00000 | register:
            continue
        if add != 0x8b020000 | (register << 5) | register:
            continue
        require(compare & 0xfffffc1f == 0xeb00001f | (register << 16), 'transition constant followed by its actual register compare')
        target_register = (compare >> 5) & 31
        require(i + 4 < len(words) and words[i + 4] & 0xff00001f == 0x54000001,
                'actual transition compare has immediate B.NE rejection before register use')
        rows.append(dict(offset=i * 4, value=((first >> 5) & 65535) | (((second >> 5) & 65535) << 16),
                         target_register=target_register))
    require(len(rows) == 5, 'exactly five independent dynamic entry/ABI/stack transition constants')
    require(not any(word & 0x0a000000 == 0x08000000 for word in words), 'copied transition has no data load or store')
    require(not any(word & 0xfc000000 == 0x94000000 or word & 0xfffffc1f in (0xd63f0000, 0xd65f0000)
                    for word in words), 'copied transition has no native call or return')
    require([word for word in words if word & 0xfffffc1f == 0xd61f0000] == [0xd61f0020]
            and words.count(0x910000bf) == 1 and words[404 // 4] == 0xd5181008
            and words[488 // 4] == 0xd61f0020,
            'one owned-SP switch, actual SCTLR retirement and sole indirect BR X1')
    return rows


def validate_transition(checks, source, base, document, binding, objects, binary, asset):
    checks.require(document['result'] == 'PASS_STANDALONE_OBJECT_AND_LINKED_PE_IDENTICAL'
                   and document['transition_sha_matches_expected_header'] is True
                   and document['transition_bytes'] == 528 and document['relocations_in_transition'] == 0,
                   'standalone and actual linked full-mode transition proof')
    expected_binary = checks.freeze(base / 'transition-prediction/transition.bin', document['transition_sha256'], 528).read_bytes()
    raw = objects['thor_fullseq_arch.obj']
    symbols, _ = coff_symbols(raw)
    names = {}
    for name in ('ThorFullSeqTransitionStart', 'ThorFullSeqTransitionEnd', 'ThorFullSeqTransitionSctlrWrite', 'ThorFullSeqTransitionKernelBranch'):
        found = [(value, section) for symbol, value, section in symbols if symbol == name]
        checks.require(len(found) == 1, 'one actual native PIC symbol: ' + name)
        names[name] = found[0]
    first, section = names['ThorFullSeqTransitionStart']
    end, end_section = names['ThorFullSeqTransitionEnd']
    checks.require(first % 4096 == 0 and section == end_section and end - first == 528,
                   'actual COFF PIC interval is one page-aligned bounded section extent')
    at = 20 + (section - 1) * 40
    size, offset, reloc = struct.unpack_from('<III', raw, at + 16)
    count = struct.unpack_from('<H', raw, at + 32)[0]
    checks.require(end <= size and offset + size <= len(raw) and reloc + count * 10 <= len(raw),
                   'actual COFF transition section and relocation bounds')
    checks.require(all(not first <= struct.unpack_from('<I', raw, reloc + i * 10)[0] < end for i in range(count)),
                   'no actual COFF relocation lies inside copied PIC bytes')
    actual = raw[offset + first:offset + end]
    checks.require(actual == expected_binary and sha(actual) == document['transition_sha256'],
                   'independent actual archived AArch64 transition equals pre-link prediction bytes')
    map_path = checks.freeze(source / BDS_RELATIVE.replace('.efi', '.map'))
    found = re.findall(r'^([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+\d+\s+ThorFullSeqTransitionStart\s*$', map_path.read_text(), re.M)
    checks.require(found and len(set(found)) == 1, 'one actual linked PE map transition start RVA')
    rva = int(found[0], 16)
    checks.require(rva == int(document['linked_pe']['transition_rva'], 16)
                   and pe_interval(binary, rva, len(actual)) == actual
                   and document['linked_pe']['pe_sha256'] == sha(binary),
                   'independent actual PE bytes equal PIC archive/prediction without hidden base relocations')
    constants = transition_immediates(actual)
    layout = binding['handoff_layout']
    expected = [(layout['Transition'], 8), (layout['Args'], 0), (asset['entry_offset'], 1),
                (asset['vector_offset'], 4), (layout['StackTop'], 5)]
    checks.require([(row['value'], row['target_register']) for row in constants] == expected,
                   'independent actual A64 MOVZ/MOVK encodes real new kernel entry, low vector, args and owned stack geometry')
    expected_header = checks.freeze(source / 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorFullSeqTransitionExpected.h',
                                    document['expected_transition_header_sha256'])
    found = re.search(r'mThorFullSeqTransitionSha256\[32\]\s*=\s*\{([^}]+)\}', expected_header.read_text())
    checks.require(found is not None and bytes.fromhex(''.join(re.findall(r'0x([0-9a-fA-F]{2})', found[1]))).hex() == sha(actual),
                   'actual compiled expected transition digest is the independently extracted PIC byte hash')
    return dict(sha256=sha(actual), bytes=len(actual), actual_PE_rva=rva, dynamic_constants=constants,
                no_data_load_store=True, no_call_or_return=True, no_relocations=True, phone_actions=0)


def validate_native_firmware(checks, source, release, documents, base, binary, asset, binding):
    detail, stack = documents['fullseq-uefi-proof.json'], documents['native-stack-usage.json']
    checks.require(detail['schema'] == 'thor-fullseq01-production-uefi-proof-v1'
                   and detail['result'] == 'PASS_ACTUAL_FULLSEQ_SOURCE_AND_LINKED_FIRMWARE_BINDING'
                   and stack['result'] == 'PASS_ACTUAL_AARCH64_OBJECTS_AND_STACK_FRAMES'
                   and detail['source_commit'] == stack['source_commit'] == release['uefi_commit'],
                   'native firmware source, retained workspace and compiler stack evidence are the actual formal build')
    native_proof_path = rooted(checks.root, asset['raw_path']).parent / 'native-build.json'
    checks.require(detail['execution_kernel_sha256'] == asset['raw_sha256']
                   and detail['execution_kernel_raw_bytes'] == asset['raw_bytes']
                   and detail['loaded_span_bytes'] == asset['loaded_span_bytes']
                   and detail['loaded_span_sha256'] == asset['loaded_span_sha256']
                   and detail['asset_manifest_sha256'] == asset['manifest_sha256']
                   and detail['execution_native_proof_sha256'] == binding['native_proof_sha256'] == hash_file(native_proof_path)
                   and detail['entry_low_runtime_geometry'] == binding['geometry']
                   and detail['handoff_layout'] == binding['handoff_layout']
                   and detail['native_cpp_inputs_sha256'] == release['native_cpp_inputs_sha256'],
                   'native PE proof, binder, full asset, merged-kernel build and all native CPP inputs form one exact closure')
    freeze_sources(checks, detail['source_files_sha256'], source)
    objects = detail['actual_native_objects']
    checks.require(objects == stack['actual_native_objects'] and len(objects) >= 15
                   and {'ThorEntryBdsDxe.obj', 'thor_fullseq_arch.obj', 'BaseUefiDecompressLib.obj',
                        'thor_payload_sha256.obj', 'sha256.obj'} <= set(objects),
                   'all actual full-sequence BDS, transition, standard decoder and SHA native objects are inventoried')
    actual_objects = {}
    for name, record in objects.items():
        library = checks.freeze(source / record['library_relative_path'], record['library_sha256'])
        original = checks.freeze(source / record['source_relative_path'], record['source_sha256'])
        raw = archive_member(library.read_bytes(), name)
        coff_symbols(raw)
        checks.require(sha(raw) == record['object_sha256'], 'independently extract exact actual AArch64 native archive member: ' + name)
        checks.require(checks.freeze(base / name, record['object_sha256']).read_bytes() == raw,
                       'public raw native object is identical to its actual linked archive member: ' + name)
        actual_objects[name] = raw
        if name == 'BaseUefiDecompressLib.obj':
            checks.require(hash_file(original) == '1eb6bcddb9ab4bafb1a0ed97ab84b5b1038350062ce883ac22876ebccf2c0bce',
                           'actual native EFI standard decompressor is the original independently executed algorithm')
    reports = stack['reports']
    checks.require(set(reports) == {name[:-4] + '.su' for name in objects if name != 'thor_fullseq_arch.obj'},
                   'actual compiler stack report covers every C native object and excludes only assembly')
    frames = {}
    for name, record in reports.items():
        report = checks.freeze(source / record['compiler_output_relative_path'], record['sha256'], record['bytes'])
        raw = report.read_bytes()
        checks.require(checks.freeze(base / 'native-stack-usage' / name, record['sha256'], record['bytes']).read_bytes() == raw,
                       'public and original native compiler stack output bytes agree: ' + name)
        parsed = parse_stack_report(raw)
        checks.require(parsed == record['functions'], 'independent native stack-frame reconstruction: ' + name)
        if name == 'ThorEntryBdsDxe.su':
            parsed = {key: value for key, value in parsed.items()
                      if key.startswith(('Fsq', 'FullSeq')) or key == 'ThorEntryBdsEntry'}
        frames.update({name + ':' + key: value for key, value in parsed.items()})
    checks.require(frames == stack['new_function_stack_bytes']
                   and max(frames.values()) == stack['maximum_new_function_stack_bytes'] < 0x4000
                   and sum(frames.values()) == stack['conservative_sum_new_function_stack_bytes'] < 0x3c000
                   and stack['shared_stack_bytes'] == 0x40000 and stack['shared_stack_guard_bytes'] == 0x4000,
                   'independent static frame sum fits guarded retained shared stack without recursive-path claims')
    checks.require(stack['C_stack_usable_base'] == 0xa7611000 and stack['C_stack_usable_end'] == 0xa764d000
                   and sum(frames.values()) < stack['C_stack_usable_end'] - stack['C_stack_usable_base']
                   and stack['copied_transition_stack_bytes'] == 0x10000
                   and stack['copied_transition_logical_guard_bytes'] == 0x4000
                   and stack['copied_transition_stack_memory_accesses'] == stack['copied_transition_C_calls'] == 0,
                   'all C pre/post work remains on guarded firmware stack; owned-SP PIC performs no C call or stack access')
    raw = actual_objects['ThorEntryBdsDxe.obj']
    symbols, sections = coff_symbols(raw)
    workspace = [(value, section) for name, value, section in symbols if name == 'mSeq']
    checks.require(len(workspace) == 1 and 1 <= workspace[0][1] <= sections, 'one actual retained mSeq workspace symbol')
    value, section = workspace[0]
    size = struct.unpack_from('<I', raw, 20 + (section - 1) * 40 + 16)[0]
    checks.require(not any(other_section == section and other_value > value for _, other_value, other_section in symbols)
                   and 0 < size - value == stack['workspace_static_bytes'] < 0x100000
                   and value == stack['workspace_coff_symbol_value']
                   and section == stack['workspace_coff_section_index'] and size == stack['workspace_coff_section_bytes'],
                   'independent actual COFF workspace storage bound, no generated estimate')
    peoff = struct.unpack_from('<I', binary, 0x3c)[0]
    checks.require(binary[peoff:peoff + 4] == b'PE\0\0'
                   and struct.unpack_from('<H', binary, peoff + 4)[0] == 0xaa64,
                   'actual linked retained BDS image is AArch64 PE')
    image_size = struct.unpack_from('<I', binary, peoff + 24 + 56)[0]
    checks.require(image_size == stack['retained_image_bytes'] and 0 < image_size < 0x200000,
                   'actual linked PE retained span matches workspace and shared-stack proof')
    for name, record in detail['components'].items():
        path = checks.root / 'artifacts/loader/thor-20261027-ios-full-seq01' / name
        content = checks.freeze(path, record['sha256'], record['bytes']).read_bytes()
        checks.require(binary.count(content) == record['occurrences_in_actual_PE'] == 1,
                       'actual linked public Apple component bytes: ' + name)
    checks.require(detail['auth_installed_in_ADT'] is False
                   and detail['trust_cache_runtime_loaded_claimed'] is False
                   and detail['post_EBS_external_source_reads'] is False
                   and detail['root_media_installed_on_phone'] is False and detail['storage_write_enabled'] is False,
                   'native public components and retained full handoff make no media install, AMFI runtime or post-EBS source claims')
    transition = validate_transition(checks, source, base, documents['fullseq-transition-binding.json'],
                                     binding, actual_objects, binary, asset)
    return dict(actual_native_objects=len(objects), actual_stack_reports=len(reports),
                maximum_function_frame_bytes=max(frames.values()), conservative_frame_sum_bytes=sum(frames.values()),
                workspace_static_bytes=size - value, retained_image_bytes=image_size,
                independent_transition=transition, phone_actions=0)


def validate_cpp_closure(checks, source, base, release, audit):
    proof, _ = checks.document(base / 'native-cpp-inputs.json', release['native_cpp_inputs_sha256'])
    checks.require(proof['schema'] == 'thor-fullseq01-all-linked-native-cpp-inputs-v1'
                   and proof['result'] == 'PASS_ACTUAL_COMPILER_DEPENDENCY_CLOSURE'
                   and proof['libraries'] == len(audit['unique_link_inputs']) == 97
                   and proof['source_commit'] == release['uefi_commit'] and proof['phone_actions'] == 0,
                   'native CPP source/header dependency closure belongs to every actual linked library')
    original = Path(audit['fd']['path'])
    for _ in Path(FD_RELATIVE).parts:
        original = original.parent
    files, reports = {}, {}
    for item in audit['unique_link_inputs']:
        artifact = item['artifact']
        old = Path(artifact['path'])
        checks.require(old.is_relative_to(original), 'native library path is within explicit original firmware root')
        library = checks.freeze(source / old.relative_to(original), artifact['sha256'], artifact['bytes'])
        for report in sorted(library.parent.rglob('*.obj.deps')):
            relative = str(report.relative_to(source))
            raw = report.read_bytes()
            reports[relative] = dict(bytes=len(raw), sha256=sha(raw))
            text = '\n'.join(line.split('#', 1)[0] for line in raw.decode().splitlines())
            require(':' in text, 'actual clang dependency report has one target separator')
            target, dependencies = text.split(':', 1)
            require(Path(target.strip()).is_relative_to(original), 'native dependency target belongs to explicit original firmware root')
            for name in dependencies.replace('\\\n', ' ').split():
                path = Path(name)
                require(path.is_absolute() and path.is_relative_to(original), 'native CPP dependency must have an explicit original firmware path')
                relative_name = str(path.relative_to(original))
                local = rooted(checks.root, source / relative_name)
                before = local.stat()
                digest = hash_file(local)
                after = local.stat()
                require((before.st_ino, before.st_size, before.st_mtime_ns) ==
                        (after.st_ino, after.st_size, after.st_mtime_ns), 'native CPP input changed during independent hashing')
                value = dict(bytes=before.st_size, sha256=digest)
                files[relative_name] = value
                previous = checks.inputs.get(str(local))
                require(previous is None or previous == value, 'native CPP input conflicts with source/object identity')
                checks.inputs[str(local)] = value
        for report in sorted(library.parent.glob('*resp*')):
            if report.is_file():
                reports[str(report.relative_to(source))] = dict(bytes=report.stat().st_size, sha256=hash_file(report))
    checks.require(files == proof['files'] and len(files) == 803,
                   'independent union of all actual native compiler reports reconstructs exact 803 source/header inputs')
    checks.require(reports == proof['dependency_reports']
                   and sum(name.endswith('.obj.deps') for name in reports) == 328 and len(reports) == 332,
                   'all 328 actual compiler dependency reports plus four response metadata records are bound')
    for name, record in reports.items():
        checks.freeze(source / name, record['sha256'], record['bytes'])
    checks.require('Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/thor_fullseq_fdt_api.h' in files,
                   'native firmware actually compiled the shared FDT API header independently tested on both hosts')
    return dict(libraries=97, cpp_inputs=len(files), object_dependency_reports=328,
                response_metadata_reports=4, actual_union_independently_reconstructed=True, phone_actions=0)


def validate_firmware(checks, args, asset, output):
    source = rooted(checks.root, args.source)
    release, release_path = checks.document(args.firmware_release)
    audit, audit_path = checks.document(args.firmware_audit, release['firmware_audit_sha256'])
    index, index_path = checks.document(args.public_transfer_index)
    checks.require(release['schema'] == 'thor-fullseq01-uefi-release-v1'
                   and release['result'].startswith('PASS') and release['phone_actions'] == 0
                   and release['full_kernel_entry_executed'] is False and release['no_unknown_FDT_RAM'] is True,
                   'formal host-only full-sequence UEFI release with unchanged RAM authority')
    for name, key in [('execution_kernel_sha256', 'raw_sha256'), ('execution_kernel_raw_bytes', 'raw_bytes'),
                      ('span_bytes', 'loaded_span_bytes'), ('loaded_span_sha256', 'loaded_span_sha256')]:
        checks.require(release[name] == asset[key], 'actual UEFI full execution-kernel identity: ' + name)
    checks.require(release['boot_args_abi'] == 'darwin22-8796' and release['boot_args_bytes'] == 736
                   and release['command_line_bytes'] == 608
                   and release['transition_sha_matches_expected_header'] is True,
                   'actual Darwin22 ABI and rebound post-EBS full-kernel transition')
    checks.require(audit['status'] == 'PASS' and audit['failures'] == []
                   and len(audit['volumes']) == 2 and len(audit['actual_modules']) == 20
                   and len(audit['unique_link_inputs']) == 97,
                   'complete two-FV twenty-PE ninety-seven-native-library firmware audit')
    fd = checks.freeze(source / FD_RELATIVE, release['uefi_fd_sha256'], 3145728)
    checks.require(audit['fd']['sha256'] == release['uefi_fd_sha256'] and audit['fd']['bytes'] == 3145728,
                   'formal firmware audit identifies actual fixed three-MiB FD')
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True)
    checks.require(head == release['uefi_commit'] == index['source_commit'] and not dirty,
                   'actual UEFI tree and transferred public compilation closure are one clean formal revision')
    checks.require(index['schema'] == 'thor-fullseq01-public-source-firmware-transfer-v1'
                   and index['contains_seed'] is False and index['contains_boot_image'] is False
                   and index['phone_actions'] == 0, 'public firmware transfer has no seed, image or hardware action')
    for record in index['audited_input_files']:
        relative = Path(record['relative_path'])
        destination = source / Path(*relative.parts[1:])
        checks.require(not relative.is_absolute() and relative.parts[0] == 'uefi-root'
                       and destination.resolve().is_relative_to(source.resolve()),
                       'explicit public archive input resolves strictly inside actual firmware source')
        checks.freeze(destination, record['sha256'], record['bytes'])
    for name in ('firmware_audit_replay.py', 'firmware_audit.py', 'firmware_parse_volumes.py'):
        checks.freeze(Path(__file__).with_name(name))
    adapter = load_module('fullseq_complete_firmware_replay', Path(__file__).with_name('firmware_audit_replay.py'))
    with tempfile.TemporaryDirectory(prefix='fullseq-firmware-replay-', dir=output) as temporary:
        fresh = adapter.replay(checks.root, source, audit_path, index_path, Path(temporary))
    checks.require(fresh['result'] == 'PASS' and fresh['modules'] == 20 and fresh['link_inputs'] == 97
                   and fresh['volumes'] == 2 and fresh['full_audit_semantics_equal_after_explicit_relocation'] is True,
                   'fresh full FD/FFS/PE/archive composition audit reproduces exact public native build inputs')
    sidecars = {
        'fullseq_uefi_proof_sha256': 'fullseq-uefi-proof.json',
        'native_stack_usage_sha256': 'native-stack-usage.json',
        'transition_binding_sha256': 'fullseq-transition-binding.json',
        'reproducible_link_sha256': 'fullseq-reproducible-link.json',
    }
    documents = {}
    for field, name in sidecars.items():
        document, path = checks.document(release_path.parent / name, release[field])
        checks.require(document.get('phone_actions') == 0, 'native firmware side proof has zero phone actions: ' + name)
        documents[name] = document
    binding, binding_path = checks.document(release_path.parent / 'binding/fullseq-binding.json',
                                            release['binding_audit_sha256'])
    payload = rooted(checks.root, asset['header_path']).read_bytes() + rooted(checks.root, asset['compressed_path']).read_bytes()
    prefix = OUTER.pack(OUTER_MAGIC, 3, 128, len(payload), hashlib.sha256(payload).digest(), bytes(64))[:64]
    checks.require(binding['result'] == 'PASS_ACTUAL_FULL_RAW_LAYOUT_AND_V3_BINDING'
                   and binding['outer_public_prefix_hex'] == prefix.hex()
                   and binding['outer_public_prefix_sha256'] == sha(prefix)
                   and binding['payload_bytes'] == len(payload) and binding['payload_sha256'] == sha(payload)
                   and binding['TC_runtime_consumption_claimed'] is False
                   and binding['auth_installed_in_adt'] is False and binding['phone_actions'] == 0,
                   'independent public v3 framing binds only new full asset and makes no private seed/TC/auth runtime claim')
    for name in ('raw_bytes', 'raw_sha256', 'loaded_span_bytes', 'loaded_span_sha256', 'segment_record_count',
                 'nonzero_segment_count', 'entry_offset', 'low_vector_offset', 'runtime_vector_offset'):
        checks.require(binding['geometry'][name] == asset[name], 'actual native binder geometry agrees with independent Mach-O: ' + name)
    for name, key in [('__start', 'entry_offset'), ('_LowExceptionVectorBase', 'low_vector_offset'),
                      ('_ExceptionVectorsBase', 'runtime_vector_offset'), ('_bootstrap_pagetables', 'boot_pt_offset')]:
        checks.require(binding['geometry']['symbol_offsets'][name] == asset[key],
                       'actual binder symbol identity agrees with independent real nlist: ' + name)
    layout = binding['handoff_layout']
    checks.require(layout['Args'] == asset['loaded_span_bytes']
                   and layout['Transition'] == layout['Args'] + 0x4000
                   and layout['StackTop'] - layout['Stack'] == 0x10000
                   and layout['AdtSlot'] == 0x4000 and layout['TrustCacheSlot'] == 0x10000
                   and layout['Used'] == layout['Log'] + 0x4000
                   and layout['Used'] <= layout['MinimumGrant'] <= 0x7000000,
                   'actual retained metadata, guarded native stack, ADT/TC/log tail fit smallest authorized 112MiB candidate')
    checks.require(documents['fullseq-uefi-proof.json']['binding_audit_sha256'] == hash_file(binding_path),
                   'actual native release and detailed proof bind the same full-kernel expected geometry')
    details = documents['fullseq-uefi-proof.json']
    checks.require(details['host_audits_sha256'] == release['host_audits_sha256'],
                   'formal release and native firmware proof bind identical production host executions')
    for key, expected in release['host_audits_sha256'].items():
        document, _ = checks.document(release_path.parent / ('host-' + key + '.json'), expected)
        checks.require('PASS' in str(document.get('result', document.get('status', '')))
                       and document.get('phone_actions') == 0,
                       'actual production firmware host proof passed without phone actions: ' + key)
        if 'source_files_sha256' in document:
            original_source = Path(audit['fd']['path'])
            for _ in Path(FD_RELATIVE).parts:
                original_source = original_source.parent
            freeze_sources(checks, document['source_files_sha256'], source, original_source)
    pe = checks.freeze(source / BDS_RELATIVE)
    binary = pe.read_bytes()
    expected = rooted(checks.root, asset['header_path']).read_bytes()
    checks.require(binary.count(expected) == 1 and bytes.fromhex(asset['loaded_span_sha256']) in binary,
                   'actual linked BDS PE embeds one selected full header and loaded-span digest')
    command_line = release['kernel_command_line']
    checks.require(isinstance(command_line, str) and 0 < len(command_line.encode('ascii')) < 608
                   and '\0' not in command_line and binary.count(command_line.encode('ascii') + b'\0') == 1
                   and release['command_line_digest_includes_terminating_NUL'] is True
                   and sha(command_line.encode('ascii') + b'\0') == release['command_line_sha256'],
                   'actual linked BDS PE contains exact bounded native kernel command-line string')
    native_firmware = validate_native_firmware(checks, source, release, documents, release_path.parent, binary, asset, binding)
    cpp_closure = validate_cpp_closure(checks, source, release_path.parent, release, audit)
    return dict(release=release, release_path=str(release_path), actual_fd_path=str(fd), fd_sha256=hash_file(fd),
                fresh_firmware_replay=fresh, side_proofs=documents, kernel_command_line=command_line,
                native_firmware=native_firmware, native_cpp_closure=cpp_closure, phone_actions=0)


def recheck_inventory(checks, document):
    pins, sizes = document['frozen_input_sha256'], document['frozen_input_bytes']
    checks.require(isinstance(pins, dict) and pins and set(pins) == set(sizes), 'complete frozen public source, proof and byte inventory')
    for name, expected in pins.items():
        path = rooted(checks.root, name)
        require(path.is_file(), 'frozen public input disappeared: ' + str(path))
        before = path.stat()
        digest = hash_file(path)
        after = path.stat()
        require(before.st_size == sizes[name] and digest == expected
                and (before.st_ino, before.st_size, before.st_mtime_ns) ==
                    (after.st_ino, after.st_size, after.st_mtime_ns), 'frozen public input changed: ' + str(path))
        record = dict(bytes=before.st_size, sha256=digest)
        require(str(path) not in checks.inputs or checks.inputs[str(path)] == record, 'frozen public input identity conflict')
        checks.inputs[str(path)] = record
    checks.require(True, 'every frozen public source, native object, library, receipt and execution input rehashed unchanged')


def validate_private_image(checks, args, asset, public):
    package = rooted(checks.root, args.package_directory)
    manifest, _ = checks.document(package / 'manifest.json')
    checks.require(manifest['schema'] == 'thor-fullseq01-one-use-boot-package-v1'
                   and manifest['run_id'] == args.run_id and manifest['entry_mode'] == 'darwin22-full'
                   and manifest['sole_boot_host'] == 'macmini' and manifest['state'] == 'HOST_PACKAGED_AWAITING_INDEPENDENT_L2',
                   'exact new Mac one-use full-sequence package identity')
    checks.require(manifest['full_kernel'] == asset and manifest['kernel_sha256'] == asset['raw_sha256']
                   and manifest['kernel_bytes'] == asset['raw_bytes'] and manifest['fd_sha256'] == public['fd_sha256'],
                   'private image binds full merged execution kernel and audited FD')
    checks.require(manifest['full_kernel_entry_planned'] is True and manifest['full_kernel_entry_executed'] is False
                   and manifest['hardware_tested'] is False and manifest['phone_attempts'] == manifest['phone_actions'] == 0
                   and manifest['seed_reuse_allowed'] is False and manifest['seed_raw_logged'] is False
                   and manifest['seed_bytes'] == 64 and manifest['packet_version'] == 3
                   and manifest['packet_header_bytes'] == 128 and manifest['boot_args_abi'] == 'darwin22-8796',
                   'one fresh full-mode seed and no pre-existing hardware execution or retry')
    checks.require(manifest['pre_generation_preflight_sha256'] == hash_file(rooted(checks.root, args.public_preflight)),
                   'package was created only after exact completed public preflight')
    image_path = checks.freeze(package / 'thor-boot.img', manifest['image_sha256'], manifest['image_bytes'])
    bundle_path = checks.freeze(package / 'kernel-bundle.bin', manifest['bundle_sha256'], manifest['bundle_bytes'])
    bundle = bundle_path.read_bytes()
    wire = parse_packet(bundle)
    for key in ('compressed_bytes', 'compressed_sha256', 'raw_bytes', 'raw_sha256', 'loaded_span_bytes',
                'algorithm_id', 'nonzero_segment_count'):
        checks.require(wire[key] == asset[key], 'actual private v3 packet full execution identity: ' + key)
    for key in ('seed_bytes', 'seed_sha256', 'payload_bytes', 'payload_sha256', 'bundle_bytes', 'bundle_sha256'):
        checks.require(wire[key] == manifest[key], 'actual one-use transport metadata: ' + key)
    payload = rooted(checks.root, asset['header_path']).read_bytes() + rooted(checks.root, asset['compressed_path']).read_bytes()
    checks.require(bundle[128:128 + wire['payload_bytes']] == payload, 'v3 packet contains only the exact independently decoded full asset')
    kernel = checks.freeze(package / 'kernel.gz-dtb').read_bytes()
    decoder = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    uncompressed = decoder.decompress(kernel, (4 << 20) + 1)
    checks.require(decoder.eof and not decoder.unconsumed_tail and len(uncompressed) == 112 + 3145728,
                   'one complete bounded gzip stream contains only stock shim and actual FD')
    shim = checks.freeze(package / 'BootShim.bin').read_bytes()
    fd = checks.freeze(package / 'SILICIUM_UEFI.fd', public['fd_sha256'], 3145728).read_bytes()
    dtb = checks.freeze(package / 'thor-stock-vendor-dtb.bin',
                        'c54e1b6efb086420761f05c2358aa71c98db5f961408399c0e1a85a408328971', 6443651).read_bytes()
    checks.require(len(shim) == 112 and uncompressed == shim + fd and decoder.unused_data == dtb,
                   'actual gzip body and exact trailing stock DTB, no second stream or injected data')
    checks.require(shim == checks.freeze(checks.root / 'artifacts/thor-smoke-01/BootShim.bin').read_bytes()
                   and dtb == checks.freeze(checks.root / 'artifacts/thor-smoke-01/thor-stock-vendor-dtb.bin').read_bytes(),
                   'actual wrapper retains unchanged original Thor shim and stock DTB')
    boot = parse_boot_v4(image_path.read_bytes(), kernel, bundle)
    checks.require(boot['image_sha256'] == manifest['image_sha256'], 'independent actual Android v4 parser reproduces image digest')
    for name, content in [('kernel', kernel), ('ramdisk', bundle)]:
        checks.require(checks.freeze(package / 'unpacked' / name).read_bytes() == content,
                       'pinned external unpack output agrees with independent parser: ' + name)
    for record in manifest['package_commands']:
        checks.require(record['returncode'] == 0, 'actual pinned package tool succeeded: ' + record['name'])
    scripts = manifest['package_tools']['scripts']
    for name, record in scripts.items():
        checks.freeze(record['path'], record['sha256'])
    checks.freeze(checks.root / 'toolchains/android-boot-tools/package-tools.json',
                  manifest['package_tools']['manifest_sha256'])
    python = manifest['package_tools']['python']
    checks.require(Path(python['path']).resolve() == Path(sys.executable).resolve()
                   and hash_file(python['path']) == python['sha256'], 'actual packaging Python host-toolchain identity')
    checks.require(not (checks.root / 'state/consumed-images' / (manifest['image_sha256'] + '.json')).exists(),
                   'new image is absent from permanent consumed-image ledger')
    checks.require(not (checks.root / 'runs' / args.run_id / 'l3').exists(), 'new run has no previous phone execution record')
    historical = 0
    for path in sorted((checks.root / 'runs').glob('*/l2/package/manifest.json')):
        if path.resolve() == (package / 'manifest.json').resolve():
            continue
        previous = json.loads(path.read_text())
        checks.require(previous.get('image_sha256') != manifest['image_sha256']
                       and previous.get('seed_sha256') != manifest['seed_sha256'],
                       'new image and seed differ from historical package: ' + path.parts[-4])
        checks.freeze(path)
        historical += 1
    for name, seed_key in [
        ('runs/thor-20261026-vm-mem01-private-transfer/mac-independent-boot-v4-check.json', 'one_use_seed_sha256'),
        ('evidence/files_needed/mem02/manual-transfer/manifest.json', 'seed_sha256'),
    ]:
        previous, _ = checks.document(name)
        checks.require(re.fullmatch('[0-9a-f]{64}', previous['image_sha256']) is not None
                       and re.fullmatch('[0-9a-f]{64}', previous[seed_key]) is not None
                       and previous['image_sha256'] != manifest['image_sha256']
                       and previous[seed_key] != manifest['seed_sha256'],
                       'new image and seed differ from original MEM01/02 public digest receipt: ' + name)
        historical += 1
    for path in sorted((checks.root / 'state/consumed-images').glob('*.json')):
        previous, _ = checks.document(path)
        checks.require(path.stem != manifest['image_sha256']
                       and previous.get('seed_sha256') != manifest['seed_sha256'],
                       'new image and seed are absent from each available permanent consume record')
    checks.require(historical >= 8, 'actual historical one-use image/seed digest comparisons cover MEM01 through MEM08')
    return dict(manifest=manifest, android_boot_v4=dict(sha256=boot['image_sha256'], bytes=boot['image_bytes']),
                wire=wire, historical_packages_checked=historical, phone_actions=0)


def public_tool_inputs(checks):
    for name in ('fullseq_pack.py', 'fullseq_asset.py', 'package_host.py', 'full_loader_asset.py',
                 'prepare_fullseq_host.py', 'fullseq_prelink_audit.py'):
        checks.freeze(Path(__file__).with_name(name))
    tools_path = checks.freeze(checks.root / 'toolchains/android-boot-tools/package-tools.json')
    tools = json.loads(tools_path.read_text())
    # The actual tool schema is checked by its production loader; all scripts and
    # the manifest are independently frozen here and final image geometry is
    # parsed by this verifier, not delegated to those tools.
    host = load_module('fullseq_public_boot_tool_reader', Path(__file__).with_name('package_host.py'))
    _, _, metadata = host.load_tools(checks.root, tools_path)
    for record in metadata['scripts'].values():
        checks.freeze(record['path'], record['sha256'])
    config = checks.freeze(checks.root / 'thor-build.toml')
    stock = tomllib.loads(config.read_text())['stock']
    checks.freeze(checks.root / 'artifacts/thor-smoke-01/BootShim.bin', stock['bootshim_sha256'], 112)
    checks.freeze(checks.root / 'artifacts/thor-smoke-01/thor-stock-vendor-dtb.bin', stock['dtb_sha256'], 6443651)
    for name, digest in (
        ('evidence/upstream/ios-full-seq01-20261026T180503Z/upstream-fetch.json',
         'db688f59caff915c8a7dcf71e659451c454c0a7a812c5d71385cf1f5d139e55a'),
        ('evidence/upstream/ios-full-seq01-20261026T180503Z/source-review.json',
         'a34c0602dfa978b0f67aeda64e7e1d605d8769e7c5f19c43d2f050979fe4999d'),
        ('evidence/upstream/ios-full-seq01-components-20261026T192504Z/component-fetch.json',
         'eb805f5fd6ff0681a7ac7d8b8e72ca093764073de5db343b81b19d0748e8a66e'),
        ('evidence/upstream/ios-full-seq01-components-20261026T192504Z/component-source-review.json',
         '6b7000cdb5edbac2f343fed6f23ceefea2433387abae4e876efe8f8162f9d829'),
    ):
        checks.freeze(checks.root / name, digest)
    return metadata


def verify_sha_reuse(checks, source):
    """Reuse the fixed SHA algorithm's real 113MiB tests, never its old asset identity."""
    document, path = checks.document(
        'runs/thor-20261026-vm-mem08/l2/full-loader-precheck-03.json',
        'bc668f719c9795a159289f8b9ef4b5a07ea8da93ebc4a900c396cbc18e962bdd')
    receipt = document['production_sha_replay']
    checks.require(document['result'].startswith('PASS')
                   and receipt['schema'] == 'thor-mem08-independent-production-sha-host-v1'
                   and receipt['result'] == 'PASS_REAL_PRODUCTION_SHA_FULL_LOADED_SPAN'
                   and receipt['tests_run'] == 8 and receipt['phone_actions'] == 0
                   and receipt['hardware_tested'] is False
                   and receipt['actual_production_ThorPayloadSha256'] is True
                   and receipt['actual_project_OpenSSL_SHA_C'] is True
                   and receipt['loaded_span_bytes'] == 113229824,
                   'fixed historical real production SHA full-span and boundary/fault execution is complete')
    expected = [('whole_loaded_span', 1), ('cross_32MiB_boundary', 1), ('wrong_digest', 0),
                ('deadline_after_32MiB', 0), ('zero_length', 0), ('window_limit', 0),
                ('one_update_over_32MiB', 1), ('many_updates_full_span', 1)]
    checks.require([(row['case'], row['result']) for row in receipt['results']] == expected,
                   'fixed SHA regression includes actual cumulative 113MiB, single large update, deadline and wrong digest')
    old_source = checks.root / 'worktrees/uefi/vm-mem08'
    freeze_sources(checks, receipt['source_files_sha256'])
    algorithm_inputs = 0
    for name, expected_hash in receipt['source_files_sha256'].items():
        original = Path(name)
        if not original.is_relative_to(old_source):
            continue
        relative = original.relative_to(old_source)
        if (str(relative).startswith('Silicon/Silicium/OpensslPkg/')
                or relative.name in ('thor_payload_sha256.c', 'thor_payload_sha256.h', 'sha256.c', 'mem_clr.c')):
            checks.freeze(Path(source) / relative, expected_hash)
            algorithm_inputs += 1
    checks.require(algorithm_inputs >= 20, 'all actual new production SHA implementation and OpenSSL dependency bytes equal tested fixed algorithm')
    host_dependencies = receipt.get('host_toolchain_dependencies_sha256', {})
    for name, digest in host_dependencies.items():
        checks.require(Path(name).is_absolute() and not Path(name).resolve().is_relative_to(checks.root)
                       and hash_file(name) == digest, 'historical SHA explicit host-toolchain dependency stays unchanged')
    return dict(historical_receipt_path=str(path), historical_receipt_sha256=hash_file(path),
                tests_reused=8, algorithm_inputs_rechecked=algorithm_inputs,
                reused_scope='unchanged ThorPayload/OpenSSL SHA algorithm only; old Mem08 wrapper and old asset identity are not qualifications for new handoff',
                freshly_executed=False, actual_new_asset_hash_claimed_by_this_reuse=False,
                host_toolchain_dependencies_sha256=host_dependencies, phone_actions=0)


def fresh_firmware_guards(checks, args, asset, firmware, output):
    source = rooted(checks.root, args.source)
    tests = source / 'Platforms/Xiaomi/thorPkg/Tests'
    jobs = [('core', 'test_fullseq_core.py', 'THOR_FULLSEQ_CORE_AUDIT', 40),
            ('proof', 'test_fullseq_proof.py', 'THOR_FULLSEQ_PROOF_AUDIT', 96),
            ('handoff', 'test_fullseq_handoff.py', 'THOR_FULLSEQ_HANDOFF_AUDIT', 64)]
    results = {}
    for name, filename, variable, count in jobs:
        suite = checks.freeze(tests / filename)
        directory = output / ('firmware-' + name)
        directory.mkdir()
        destination = directory / 'execution.json'
        env = dict(os.environ)
        env[variable] = str(destination)
        if name == 'handoff':
            env['THOR_FULLSEQ_HANDOFF_PROJECT_ROOT'] = str(checks.root)
            env['THOR_FULLSEQ_HANDOFF_ASSET'] = str(rooted(checks.root, args.asset_manifest))
            env['THOR_FULLSEQ_HANDOFF_BINDING'] = str(Path(firmware['release_path']).parent / 'binding/fullseq-binding.json')
            env['THOR_FULLSEQ_HANDOFF_TRANSITION'] = str(Path(firmware['release_path']).parent / 'transition-prediction/transition.bin')
        log = run_command(checks, [sys.executable, '-B', suite], directory / 'execution.log', cwd=source, env=env)
        checks.require(re.search(r'Ran\s+' + str(count) + r'\s+tests?\b', log.read_text()) is not None
                       and re.search(r'(?m)^OK\s*$', log.read_text()) is not None,
                       'fresh production firmware suite has actual successful unittest count: ' + name)
        document, _ = checks.document(destination)
        checks.require(document.get('phone_actions') == 0, 'fresh firmware host execution makes no phone actions: ' + name)
        if 'result' in document:
            checks.require(str(document['result']).startswith('PASS'), 'fresh actual firmware host result: ' + name)
        freeze_sources(checks, document['source_files_sha256'], source)
        results[name] = document
    checks.require(results['proof']['result'] == 'PASS_FULLSEQ_SERVICE_FREE_PROOF_REAL_MMU',
                   'fresh service-free handoff proof uses actual production MMU verifier')
    handoff = results['handoff']
    checks.require(handoff['result'] == 'PASS_FULLSEQ_LOAD_AND_HANDOFF'
                   and handoff['full_kernel_entered'] is False and handoff['production_seed_generated'] is False,
                   'fresh actual load/handoff executes owned-memory expansion and uses only fixed test entropy')
    return results


def validate_remote_adapter(checks, source):
    base = checks.root / 'evidence/build/ios-full-seq01'
    document, _ = checks.document(base / 'gate-i386-adapter-fresh-01/proof.json',
                                  '240bff7f270a1c5fe5c415b33145043e8665b3d2320cfd7055263e5043fb1c8e')
    index, index_path = checks.document(base / 'gate-i386-adapter-transfer-01/index.json',
                                        'c7d8be898bbf70ee593743face23242ad195809b8a317f36b4c613cb6395312e')
    imported, _ = checks.document(base / 'gate-i386-adapter-transfer-01/import-proof.json')
    checks.require(imported['result'] == 'PASS_EXACT_I386_BYTES_IMPORTED'
                   and imported['index_sha256'] == hash_file(index_path)
                   and imported['archive_sha256'] == index['archive_sha256']
                   and imported['execution_host'] == 'i386' and imported['mac_executed_adapter_ELF'] is False
                   and imported['phone_actions'] == index['phone_actions'] == 0
                   and index['real_seed_included'] is False and index['real_boot_image_included'] is False,
                   'independently executed i386 adapter evidence transfers exact bytes; Mac cannot claim executing x86 ELF')
    for name, record in index['files'].items():
        relative = Path(name)
        checks.require(not relative.is_absolute() and relative.parts[0] == 'gate-i386-adapter-fresh-01'
                       and '..' not in relative.parts, 'bounded exact independent remote host evidence member')
        checks.freeze(base / relative, record['sha256'], record['bytes'])
    checks.require(document['schema'] == 'thor-fullseq01-production-adapter-host-v1'
                   and document['result'] == 'PASS_PRODUCTION_ADAPTER_ENTRY_AND_EBS_CONTROL_FLOW'
                   and document['total'] == document['passed'] == len(document['cases']) == 33
                   and all(case['passed'] is True for case in document['cases'])
                   and document['production_expected_used'] is False
                   and document['test_entropy_is_fixed_fixture_not_production_seed'] is True
                   and document['native_kernel_execution_claimed'] is False and document['phone_actions'] == 0,
                   'actual production adapter/entry control flow has 33 successful host fault/success cases and explicit synthetic geometry boundary')
    for name, digest in document['source_files_sha256'].items():
        if name.startswith(('Platforms/', 'Mu_Basecore/')):
            path = Path(source) / name
        else:
            checks.require(name.startswith(('evidence/build/vm-mem08/host-codec/source/',
                                            'evidence/build/ios-full-seq01/adt/source-fixtures/')),
                           'adapter closure uses only explicit project fixture/codec namespaces')
            path = checks.root / name
        checks.freeze(path, digest)
    directory = base / 'gate-i386-adapter-fresh-01/proof.build'
    executable = checks.freeze(directory / 'fullseq-adapter-host', document['host_executable_sha256'])
    code = executable.read_bytes()
    checks.require(code[:4] == b'\x7fELF' and code[4] == 2 and code[5] == 1
                   and struct.unpack_from('<H', code, 18)[0] == 62,
                   'receipt binds the actual x86_64 host ELF, separate from the linked AArch64 phone PE')
    for name, digest in document['fixture_files_sha256'].items():
        checks.freeze(directory / name, digest)
    frames = document['renderer_frames']
    checks.require(len(frames) == 504 and all(frame['y'] >= 384 for frame in frames),
                   'all actual rendered host HUD events preserve reserved native top rows')
    success = {case['number']: case['observed'] for case in document['cases'] if case['number'] in (0, 10)}
    checks.require(set(success) == {0, 10}, 'actual initial-key and stale-key-retry success paths are both exercised')
    for number, row in success.items():
        checks.require(row['terminal'] == 2 and row['post'] == 1 and row['marked'] == 1
                       and row['forbidden'] == row['trace_bad'] == row['post_source_at'] == 0
                       and row['post_exec0'] == row['post_exec1'] == row['post_sync_set'] == 0
                       and row['tpl_raise'] == row['tpl_restore'] == 4
                       and row['pre_proofs'] == 3 and row['post_proofs'] == 1,
                       'actual EBS/one-use/post-proof ordering and protected-source success path: ' + str(number))
    return dict(result=document['result'], execution_host='i386', tests=33, source_inputs=len(document['source_files_sha256']),
                renderer_events=len(frames), actual_AArch64_geometry_used=False,
                actual_geometry_verified_by_separate_handoff_suite=True, Mac_skip_counted_as_pass=False,
                phone_actions=0)


def public_preflight(checks, args, asset, geometry, paths, work):
    gate_test = checks.freeze(Path(__file__).resolve().parents[2] / 'tests/test_fullseq_candidate_gate.py')
    run_command(checks, [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', gate_test.parent,
                         '-p', gate_test.name, '-v'], work / 'gate-fault-tests.log')
    aggregate, native = validate_native(checks, args.candidate, args.xnu_source, geometry)
    preservation = validate_source_preservation(checks, args.xnu_source, work)
    codec = fresh_codec(checks, asset, paths, work)
    prelink = fresh_prelink(checks, args.candidate, work)
    guards = fresh_native_guards(checks, args.xnu_source, paths['raw'], native, work)
    system = validate_system_inputs(checks, native)
    firmware = validate_firmware(checks, args, asset, work)
    sha_regression = verify_sha_reuse(checks, rooted(checks.root, args.source))
    firmware_guards = fresh_firmware_guards(checks, args, asset, firmware, work)
    adapter_guards = validate_remote_adapter(checks, rooted(checks.root, args.source))
    adt = fresh_adt(checks, rooted(checks.root, args.source), work)
    display = validate_display(checks, args.xnu_source, paths['raw'], firmware['kernel_command_line'])
    tools = public_tool_inputs(checks)
    return dict(native_build=aggregate, native_selfbuild_sha256=aggregate['native_kernel_sha256'],
                source_preservation=preservation, production_codec_replay=codec, prelink=prelink,
                native_guard_replays=guards, system_inputs=system, firmware=firmware, adt=adt,
                firmware_guard_replays=firmware_guards, unchanged_sha_algorithm_regression=sha_regression,
                remote_adapter_replay=adapter_guards,
                display=display, package_tools=tools, fd_sha256=firmware['fd_sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--asset-manifest', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--structure-only', action='store_true')
    modes.add_argument('--native-only', action='store_true')
    modes.add_argument('--preflight-public', action='store_true')
    modes.add_argument('--verify-package', action='store_true')
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--xnu-source', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--firmware-release', type=Path)
    parser.add_argument('--firmware-audit', type=Path)
    parser.add_argument('--public-transfer-index', type=Path)
    parser.add_argument('--public-preflight', type=Path)
    parser.add_argument('--public-preflight-sha256')
    parser.add_argument('--package-directory', type=Path)
    parser.add_argument('--run-id', default='thor-20261027-ios-full-seq01')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = dict(schema='thor-fullseq01-asset-structure-only-v1',
                  ready_for_packaging=False, seed_generated=False, phone_actions=0)
    checks = Checks(args.root)
    try:
        args.out = rooted(checks.root, args.out)
        checks.require(not args.out.exists(), 'new immutable independent verification receipt')
        args.out.parent.mkdir(parents=True, exist_ok=True)
        checks.freeze(Path(__file__).resolve())
        checks.freeze(args.asset_manifest)
        canonical, geometry, paths = inspect_asset(args.root, args.asset_manifest)
        for path in paths.values():
            checks.freeze(path)
        result.update(result='PASS_STRUCTURAL_ASSET_ONLY', full_kernel=canonical,
                      geometry=geometry, production_codec_freshly_replayed=False)
        if args.native_only or args.preflight_public:
            checks.require(args.candidate is not None and args.xnu_source is not None,
                           'native validation requires real candidate and source')
            work = args.out.parent / (args.out.stem + '-execution')
            work.mkdir()
            if args.native_only:
                aggregate, native = validate_native(checks, args.candidate, args.xnu_source, geometry)
                preservation = validate_source_preservation(checks, args.xnu_source, work)
                codec = fresh_codec(checks, canonical, paths, work)
                prelink = fresh_prelink(checks, args.candidate, work)
                guards = fresh_native_guards(checks, args.xnu_source, paths['raw'], native, work)
                adt = fresh_adt(checks, None, work)
                system = validate_system_inputs(checks, native)
                result.update(schema='thor-fullseq01-native-input-precheck-v1', result='PASS_NATIVE_INPUTS_ONLY',
                              native_build=aggregate, source_preservation=preservation, production_codec_replay=codec,
                              prelink=prelink, native_guard_replays=guards, adt=adt, system_inputs=system,
                              production_codec_freshly_replayed=True)
            else:
                checks.require(all(getattr(args, name) is not None for name in
                                   ('source', 'firmware_release', 'firmware_audit', 'public_transfer_index')),
                               'formal public preflight requires complete actual firmware release, audit and source transfer')
                verified = public_preflight(checks, args, canonical, geometry, paths, work)
                result.update(verified, schema='thor-fullseq01-public-preflight-v1', result='PASS_PUBLIC_FULLSEQ01',
                              ready_for_packaging=True, production_codec_freshly_replayed=True,
                              verifier_path=str(Path(__file__).resolve()), verifier_sha256=hash_file(__file__),
                              full_kernel_entry_executed=False, hardware_test_attempts=0)
        elif args.verify_package:
            checks.require(args.run_id == 'thor-20261027-ios-full-seq01'
                           and args.public_preflight is not None and args.public_preflight_sha256 is not None,
                           'exact new full-sequence run and completed public preflight are required')
            public, _ = checks.document(args.public_preflight, args.public_preflight_sha256)
            checks.require(public['schema'] == 'thor-fullseq01-public-preflight-v1'
                           and public['result'] == 'PASS_PUBLIC_FULLSEQ01' and public['ready_for_packaging'] is True
                           and public['seed_generated'] is False and public['phone_actions'] == 0
                           and public['full_kernel_entry_executed'] is False
                           and public['full_kernel'] == canonical and public['verifier_sha256'] == hash_file(__file__)
                           and all(row['passed'] is True for row in public['checks']),
                           'formal public preflight is complete, from exact current verifier and selected full asset')
            recheck_inventory(checks, public)
            if args.package_directory is None:
                args.package_directory = checks.root / 'runs' / args.run_id / 'l2/package'
            checks.require(rooted(checks.root, args.package_directory) == checks.root / 'runs' / args.run_id / 'l2/package',
                           'one exact new private package directory')
            private = validate_private_image(checks, args, canonical, public)
            result.update(schema='thor-fullseq01-independent-offline-v1', result='PASS', run_id=args.run_id,
                          ready_for_packaging=False, ready_for_one_use_temporary_boot=True,
                          full_kernel_entry_executed=False, hardware_test_attempts=0,
                          inputs=dict(full_kernel=canonical,
                                      native_xnu=dict(sha256=canonical['raw_sha256'], bytes=canonical['raw_bytes']),
                                      native_selfbuild=dict(sha256=public['native_selfbuild_sha256']),
                                      android_boot_v4=private['android_boot_v4'],
                                      uefi_fd=dict(sha256=public['fd_sha256'], bytes=3145728)),
                          public_preflight_sha256=args.public_preflight_sha256,
                          prior_public_validation_replayed_now=False, frozen_public_inputs_rehashed_now=True,
                          production_codec_freshly_replayed=False, package=private,
                          trustcache_status='TC_INPUT_STAGED_RUNTIME_NOT_QUALIFIED',
                          foreign_kernel_abi_qualified=False, runtime_qualified=False,
                          user_exec_is_gui=False, gpu_acceleration_qualified=False)
    except Exception as exc:
        result.update(result='FAIL', error_type=type(exc).__name__, error=str(exc))
    result.update(checks=checks.rows, **checks.inventory())
    result.update(verifier_path=str(Path(__file__).resolve()), verifier_sha256=hash_file(__file__),
                  checked_at=datetime.now(timezone.utc).isoformat(),
                  host_toolchain_dependencies_sha256={str(Path(sys.executable).resolve()): hash_file(sys.executable)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(path=str(args.out), result=result['result'], schema=result['schema'],
                         checks=len(checks.rows), frozen_inputs=len(checks.inputs),
                         sha256=hash_file(args.out), error=result.get('error'))))
    if result['result'] == 'FAIL':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
