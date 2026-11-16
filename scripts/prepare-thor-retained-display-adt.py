#!/usr/bin/env python3
"""Stage a Thor retained-display ADT for the matching development firmware."""
import argparse
from pathlib import Path
import struct

from pong_adt_contract import parse_adt


def emit(nodes, path):
    entries = nodes[path]
    children = [
        key for key in nodes
        if key != '/' and key.rsplit('/', 1)[0] == path.rstrip('/')
    ]
    result = bytearray(struct.pack('<II', len(entries), len(children)))
    for key, value in entries.items():
        result += struct.pack('<32sI', key.encode('ascii'), len(value))
        result += value
        result += bytes(-len(value) % 4)
    for child in children:
        result += emit(nodes, child)
    return result


def prepare(blob):
    nodes = parse_adt(blob)
    if emit(nodes, '/') != blob:
        raise ValueError('Cannot reproduce original ADT encoding')
    chosen = nodes['/chosen']
    if 'thor-native-scanout' in chosen:
        raise ValueError('Scanout property already exists')
    # DarwinBoot fills the address and length after it reserves the lease.
    chosen['thor-native-scanout'] = bytes(16)

    if '/chosen/machine-timeouts' in nodes or '/machine-timeouts' in nodes:
        raise ValueError('Machine-timeouts node already exists')
    nodes['/chosen/machine-timeouts'] = {
        'name': b'machine-timeouts\0',
        'interrupt-masked-debug-mode': struct.pack('<I', 1),
    }
    packed = emit(nodes, '/')
    if parse_adt(packed) != nodes:
        raise ValueError('Thor retained-display properties failed ADT round-trip')
    return packed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    options = parser.parse_args()
    packed = prepare(options.input.read_bytes())
    with options.output.open('xb') as output:
        output.write(packed)


if __name__ == '__main__':
    main()
