"""Public full-kernel codec assets and the FULLSEQ01 transport contract."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import subprocess
from full_loader_asset import HEADER, MAGIC, macho_layout

OUTER = struct.Struct('<16sIIQ32s64s')
OUTER_MAGIC = b'THOR-FULLSEQ01\0\0'
MAX_COMPRESSED = 20 * 1024 * 1024


def require(value, message):
    if not value:
        raise RuntimeError('FULLSEQ01: ' + message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rooted(root, name):
    result = (Path(root) / name).resolve()
    require(result.is_relative_to(Path(root).resolve()), 'input remains inside project')
    return result


def read_inner(data):
    require(len(data) >= 136, 'complete inner header and codec header')
    magic, version, size, count, digest, raw, span, rawsha, algorithm, segments, reserved = HEADER.unpack(data[:128])
    require((magic, version, size, algorithm, reserved) == (MAGIC, 1, 128, 1, 0), 'standard EFI inner format')
    require(8 < count <= MAX_COMPRESSED and count == len(data) - 128, 'exact bounded compressed body')
    require(0 < raw <= 64 * 1024 * 1024 and raw <= span <= 112 * 1024 * 1024,
            'actual raw file and staged span bounds')
    require(0 < segments <= 32 and sha(data[128:]) == digest.hex(), 'body SHA and segment count')
    inner, output = struct.unpack_from('<II', data, 128)
    require(inner == count - 8 and output == raw, 'real standard EFI compression header')
    return dict(compressed_bytes=count, compressed_sha256=digest.hex(), raw_bytes=raw,
                raw_sha256=rawsha.hex(), loaded_span_bytes=span, algorithm_id=algorithm,
                nonzero_segment_count=segments)


def execution_identity(raw, geometry):
    count = struct.unpack_from('<I', raw, 16)[0]
    at = 32; tables = []; entries = []; executable = []
    for _ in range(count):
        command, size = struct.unpack_from('<II', raw, at)
        if command == 2:
            require(size == 24, 'complete Mach-O symbol command')
            tables.append(struct.unpack_from('<4I', raw, at + 8))
        if command == 5:
            require(size >= 288 and struct.unpack_from('<II', raw, at + 8) == (6, 68),
                    'real ARM64 UNIXTHREAD entry state')
            entries.append(struct.unpack_from('<Q', raw, at + 272)[0])
        if command == 0x19 and struct.unpack_from('<I', raw, at + 60)[0] & 4:
            va, _, _, filebytes = struct.unpack_from('<4Q', raw, at + 24)
            executable.append((va, va + filebytes))
        at += size
    require(len(tables) == len(entries) == 1, 'one actual native symbol table and cold entry')
    symoff, count, stroff, strsize = tables[0]
    require(symoff + count * 16 <= len(raw) and stroff + strsize <= len(raw), 'actual symbol-table bounds')
    strings = raw[stroff:stroff + strsize]; symbols = {}
    for index in range(count):
        pos, kind, _, _, va = struct.unpack_from('<IBBHQ', raw, symoff + index * 16)
        if kind & 0xe0 or kind & 0x0e != 0x0e:
            continue
        require(pos < len(strings), 'native symbol string index')
        end = strings.find(b'\0', pos); require(end >= pos, 'terminated native symbol name')
        name = strings[pos:end].decode()
        symbols.setdefault(name, set()).add(va)
    base = geometry['virtual_base']
    require(any(low <= entries[0] < high for low, high in executable), 'cold entry in executable bytes')
    result = dict(entry_offset=entries[0] - base)
    for key, name in [('low_vector_offset', '_LowExceptionVectorBase'),
                      ('runtime_vector_offset', '_ExceptionVectorsBase'),
                      ('boot_pt_offset', '_bootstrap_pagetables')]:
        require(name in symbols and len(symbols[name]) == 1, 'unique actual native symbol ' + name)
        va = next(iter(symbols[name])); require(base <= va < geometry['virtual_end'], 'native symbol inside span')
        result[key] = va - base
    result['vector_offset'] = result['low_vector_offset']
    return result


def encode_v3(payload, seed):
    read_inner(payload)
    require(isinstance(seed, bytes) and len(seed) == 64 and any(seed), 'one-use seed has64 bytes')
    value = OUTER.pack(OUTER_MAGIC, 3, 128, len(payload), hashlib.sha256(payload).digest(), seed) + payload
    return value + bytes((-len(value)) % 4096)


def decode_v3(bundle):
    require(len(bundle) >= 264 and len(bundle) % 4096 == 0, 'page-padded complete v3 bundle')
    magic, version, header, size, digest, seed = OUTER.unpack(bundle[:128])
    require((magic, version, header) == (OUTER_MAGIC, 3, 128), 'full-only transport identity')
    require(136 <= size <= MAX_COMPRESSED + 128 and size <= len(bundle) - 128,
            'bounded complete v3 payload')
    require(len(bundle) == ((128 + size + 4095) // 4096) * 4096 and not any(bundle[128 + size:]),
            'exact zero tail padding')
    require(any(seed) and sha(bundle[128:128 + size]) == digest.hex(), 'fresh seed and outer body SHA')
    return read_inner(bundle[128:128 + size])


def validate(root, manifest_path):
    root = Path(root).resolve(); manifest_path = rooted(root, manifest_path)
    document = json.loads(manifest_path.read_text())
    require(document.get('schema') == 'thor-fullseq01-full-kernel-asset-manifest-v1', 'asset manifest schema')
    require(document.get('raw_kernel_path') == document.get('raw_path'), 'one bound raw kernel path')
    raw_path = rooted(root, document['raw_path']); raw = raw_path.read_bytes()
    comp_path = rooted(root, document['compressed_path']); compressed = comp_path.read_bytes()
    header_path = rooted(root, document['header_path']); header = header_path.read_bytes()
    fields = read_inner(header + compressed)
    require(len(raw) == fields['raw_bytes'] and sha(raw) == fields['raw_sha256'], 'actual full raw identity')
    geometry = macho_layout(raw)
    identity = execution_identity(raw, geometry)
    for key, value in fields.items():
        require(document.get(key) == value, 'header and manifest agree: ' + key)
    for key in ('loaded_span_bytes', 'loaded_span_sha256', 'segment_record_count', 'nonzero_segment_count'):
        require(document.get(key) == geometry[key], 'actual Mach-O layout: ' + key)
    require(document.get('segments') == geometry['segments'], 'exact original load commands and segments')
    require(all(document.get(key) == value for key, value in identity.items()), 'actual cold and runtime entry identities')
    require(document.get('header_sha256') == sha(header) and len(header) == 128, 'exact compiled inner header')
    require(document.get('codec_roundtrip') is True and document.get('scratch_bytes') == 13376,
            'actual standard production codec roundtrip')
    return dict(schema='thor-fullseq01-validated-full-kernel-v1',
        manifest_path=str(manifest_path.relative_to(root)), manifest_sha256=sha(manifest_path.read_bytes()),
        raw_path=str(raw_path.relative_to(root)), raw_kernel_path=str(raw_path.relative_to(root)),
        compressed_path=str(comp_path.relative_to(root)),
        header_path=str(header_path.relative_to(root)), header_sha256=sha(header), **fields,
        loaded_span_sha256=geometry['loaded_span_sha256'], segment_record_count=geometry['segment_record_count'],
        scratch_bytes=13376, **identity, full_kernel_entry_executed=False)


def produce(root, kernel, codec, codec_sha256, out):
    root = root.resolve(); kernel = rooted(root, kernel); codec = rooted(root, codec); out = rooted(root, out)
    require(sha(codec.read_bytes()) == codec_sha256, 'pinned production codec executable')
    raw = kernel.read_bytes(); geometry = macho_layout(raw); identity = execution_identity(raw, geometry)
    require(0 < len(raw) <= 64 * 1024 * 1024, 'bounded real full kernel')
    out.mkdir(parents=True, exist_ok=False)
    comp = out / 'full-kernel.efi'
    process = subprocess.run([str(codec), str(kernel), str(comp)], capture_output=True, text=True, timeout=180)
    (out / 'codec.stdout.log').write_text(process.stdout); (out / 'codec.stderr.log').write_text(process.stderr)
    require(process.returncode == 0, 'actual production compression and decompression')
    result = json.loads(process.stdout)
    compressed = comp.read_bytes()
    require(result.get('roundtrip') is True and result.get('raw_bytes') == len(raw) and
            result.get('compressed_bytes') == len(compressed) and result.get('scratch_bytes') == 13376,
            'codec roundtrip receipt matches actual files')
    header = HEADER.pack(MAGIC, 1, 128, len(compressed), hashlib.sha256(compressed).digest(),
        len(raw), geometry['loaded_span_bytes'], hashlib.sha256(raw).digest(), 1,
        geometry['nonzero_segment_count'], 0)
    (out / 'full-kernel.header.bin').write_bytes(header)
    document = dict(schema='thor-fullseq01-full-kernel-asset-manifest-v1', raw_path=str(kernel.relative_to(root)),
        raw_kernel_path=str(kernel.relative_to(root)),
        compressed_path=str(comp.relative_to(root)), header_path=str((out / 'full-kernel.header.bin').relative_to(root)),
        header_sha256=sha(header), **read_inner(header + compressed),
        loaded_span_sha256=geometry['loaded_span_sha256'], segment_record_count=geometry['segment_record_count'],
        segments=geometry['segments'], **identity, codec_path=str(codec.relative_to(root)), codec_sha256=codec_sha256,
        scratch_bytes=13376, codec_roundtrip=True, phone_actions=0)
    path = out / 'full-kernel-asset.json'; path.write_text(json.dumps(document, indent=2) + '\n')
    value = validate(root, path); print(json.dumps(value, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root', 'kernel', 'codec', 'out'): parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--codec-sha256', required=True)
    args = parser.parse_args(); produce(args.root, args.kernel, args.codec, args.codec_sha256, args.out)
