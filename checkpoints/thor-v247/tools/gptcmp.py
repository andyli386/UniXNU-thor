#!/usr/bin/env python3.13
"""Compare the primary and the backup GPT read raw from a UFS LUN.
   gptcmp.py <first-blocks.bin> <last-blocks.bin> [blocks at each end] [--diff]
The blocks are 4096 bytes (the port's LUN 4 geometry).  Prints both headers, checks their CRCs, and
compares the two partition entry arrays byte by byte."""
import struct, sys, zlib, uuid

def le(b, o, n): return int.from_bytes(b[o:o + n], 'little')
def header(b):
    if b[:8] != b'EFI PART': return None
    size = le(b, 12, 4)
    crc = le(b, 16, 4)
    ok = zlib.crc32(b[:size][:16] + b'\0' * 4 + b[20:size]) & 0xffffffff == crc
    return dict(size=size, crc_ok=ok, my=le(b, 24, 8), alt=le(b, 32, 8), first=le(b, 40, 8), last=le(b, 48, 8),
                guid=uuid.UUID(bytes_le=b[56:72]), entries_lba=le(b, 72, 8), count=le(b, 80, 4),
                esize=le(b, 84, 4), table_crc=le(b, 88, 4))
def entries(b, count, esize): return b[:count * esize]

def main():
    first = open(sys.argv[1], 'rb').read(); last = open(sys.argv[2], 'rb').read()
    n = int(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith('--') else len(first) // 4096
    B = 4096
    ph_b = first[1 * B:2 * B]; ph = header(ph_b)
    print('protective MBR at LBA 0:', first[:16].hex(), 'type byte', hex(first[450]), 'signature', first[510:512].hex())
    print('primary header:', ph)
    if not ph: return
    pe = entries(first[2 * B:], ph['count'], ph['esize'])
    pe_blocks = (ph['count'] * ph['esize'] + B - 1) // B
    print('primary entries: %d x %d bytes = %d bytes; crc=%08x (header says %08x) %s' % (
        ph['count'], ph['esize'], len(pe), zlib.crc32(pe) & 0xffffffff, ph['table_crc'],
        'MATCH' if zlib.crc32(pe) & 0xffffffff == ph['table_crc'] else 'DIFFER'))
    bh_b = last[-B:]; bh = header(bh_b)
    print('backup header:', bh)
    if not bh: return
    bel = bh['entries_lba']
    off = bel - (n and (len(last) // B)) ; off = len(last) - (n * B) + 0
    start = bel * B - (len(last) - len(last))  # placeholder
    # the last-blocks file covers LBAs [last_lba + 1 - n, last_lba]; last_lba == bh['my']
    base = bh['my'] + 1 - n
    be = last[(bel - base) * B: (bel - base) * B + ph['count'] * ph['esize']]
    print('backup entries at LBA %d (file holds LBAs %d..%d): crc=%08x (header says %08x) %s' % (
        bel, base, bh['my'], zlib.crc32(be) & 0xffffffff, bh['table_crc'],
        'MATCH' if zlib.crc32(be) & 0xffffffff == bh['table_crc'] else 'DIFFER'))
    print('disk GUID equal:', ph['guid'] == bh['guid'], '| table CRC primary %08x, backup %08x: %s' % (
        ph['table_crc'], bh['table_crc'], 'EQUAL' if ph['table_crc'] == bh['table_crc'] else 'DIFFER'))
    same = pe == be
    print('entry arrays identical:', same)
    if not same:
        for i in range(ph['count']):
            a, b = pe[i * ph['esize']:(i + 1) * ph['esize']], be[i * ph['esize']:(i + 1) * ph['esize']]
            if a != b:
                name = a[56:128].decode('utf-16le', 'replace').split('\0')[0]
                diffs = [k for k in range(ph['esize']) if a[k] != b[k]]
                print('  entry %d %-20s differs at byte offsets %s' % (i, name, diffs[:16]))
                print('     primary: type=%s unique=%s first=%d last=%d attrs=0x%016x' % (
                    uuid.UUID(bytes_le=a[0:16]), uuid.UUID(bytes_le=a[16:32]), le(a, 32, 8), le(a, 40, 8), le(a, 48, 8)))
                print('     backup : type=%s unique=%s first=%d last=%d attrs=0x%016x' % (
                    uuid.UUID(bytes_le=b[0:16]), uuid.UUID(bytes_le=b[16:32]), le(b, 32, 8), le(b, 40, 8), le(b, 48, 8)))
                print('     primary bytes 0..128:', a[:128].hex())
                print('     backup  bytes 0..128:', b[:128].hex())
    print('--- partitions (primary):')
    for i in range(ph['count']):
        a = pe[i * ph['esize']:(i + 1) * ph['esize']]
        if a[:16] == b'\0' * 16: continue
        name = a[56:128].decode('utf-16le', 'replace').split('\0')[0]
        print('  %2d %-28s first=%-10d last=%-10d blocks=%-8d attrs=0x%016x' % (i, name, le(a, 32, 8), le(a, 40, 8), le(a, 40, 8) - le(a, 32, 8) + 1, le(a, 48, 8)))

main()
