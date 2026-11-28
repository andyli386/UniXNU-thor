#!/usr/bin/env python3.13
"""Plan the one change to the partition table of the firmware LUN that marks boot slot a as booted
successfully.  Files in, files out: this tool touches no device.

    gpt-mark-ok.py <first-8-blocks.bin> <last-8-blocks.bin> <output directory>

The inputs are the first and the last eight 4096-byte blocks of the firmware LUN (LUN 4 of this
phone: 713728 blocks, read raw through TWRP by read-gpt.sh).  The tool
  1. checks that both tables are what was read on 2026-11-27: valid headers, entries whose CRC the
     headers name, identical entry arrays, the expected disk GUID, boot_a where it was;
  2. changes one thing in boot_a's attribute: the retry count back to 7 and the "successful" bit set
     (0x0037000000000000 -> 0x007F000000000000, the pattern the other _a partitions carry);
  3. writes the four blocks that change (both entry arrays' first block and both headers), the two
     eight-block images expected afterwards, and plan.json (with every SHA-256) into the output directory.
It refuses anything else: another layout, a table that is not in order, or a slot that is already
marked successful (then nothing is written and it exits with 3).
"""
import hashlib
import json
import struct
import sys
import uuid
import zlib
from pathlib import Path

B = 4096
BLOCKS = 713728                 # the firmware LUN as the kernel of the port has it pinned
ENTRIES, ESIZE = 96, 128
DISK_GUID = uuid.UUID('97b0659c-4a7d-801f-59d5-838179460ca2')
BOOT_A = 13                    # entry index of boot_a
BOOT_A_FIRST, BOOT_A_LAST = 131334, 180485
TRIES = 7 << 51                # bits 51..53: retries left
SUCCESSFUL = 1 << 54           # bit 54: booted successfully
UNVERIFIED = (0x0037000000000000, 0x003F000000000000)   # retries 6 or 7, not successful
MARKED = 0x007F000000000000


def le(b, o, n):
    return int.from_bytes(b[o:o + n], 'little')


def crc(b):
    return zlib.crc32(b) & 0xffffffff


def header(block):
    if block[:8] != b'EFI PART':
        raise SystemExit('not a GPT header: ' + block[:8].hex())
    size = le(block, 12, 4)
    fixed = block[:16] + b'\0' * 4 + block[20:size]
    if crc(fixed) != le(block, 16, 4):
        raise SystemExit('header CRC wrong')
    return dict(my=le(block, 24, 8), alt=le(block, 32, 8), first=le(block, 40, 8), last=le(block, 48, 8),
                guid=uuid.UUID(bytes_le=block[56:72]), entries=le(block, 72, 8), count=le(block, 80, 4),
                esize=le(block, 84, 4), table_crc=le(block, 88, 4))


def with_header_crc(block):
    size = le(block, 12, 4)
    block = block[:16] + b'\0' * 4 + block[16 + 4:]
    return block[:16] + struct.pack('<I', crc(block[:16] + b'\0' * 4 + block[20:size])) + block[20:]


def name_of(entry):
    return entry[56:128].decode('utf-16le', 'replace').split('\0')[0]


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        raise SystemExit(2)
    first, last, out = Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes(), Path(sys.argv[3])
    if len(first) != 8 * B or len(last) != 8 * B:
        raise SystemExit('inputs must hold eight 4096-byte blocks each')
    ph, bh = header(first[B:2 * B]), header(last[7 * B:8 * B])
    expect = dict(my=1, alt=BLOCKS - 1, first=6, last=BLOCKS - 6, entries=2, count=ENTRIES, esize=ESIZE)
    for key, value in expect.items():
        if ph[key] != value:
            raise SystemExit('primary header: %s is %s, expected %s' % (key, ph[key], value))
    expect_b = dict(my=BLOCKS - 1, alt=1, first=6, last=BLOCKS - 6, entries=BLOCKS - 5, count=ENTRIES, esize=ESIZE)
    for key, value in expect_b.items():
        if bh[key] != value:
            raise SystemExit('backup header: %s is %s, expected %s' % (key, bh[key], value))
    if ph['guid'] != DISK_GUID or bh['guid'] != DISK_GUID:
        raise SystemExit('disk GUID is not the one of the firmware LUN: %s / %s' % (ph['guid'], bh['guid']))
    n = ENTRIES * ESIZE
    pe = first[2 * B:2 * B + n]
    base = BLOCKS - 8                                   # the last-8 file holds blocks 713720..713727
    be = last[(BLOCKS - 5 - base) * B:(BLOCKS - 5 - base) * B + n]
    if crc(pe) != ph['table_crc'] or crc(be) != bh['table_crc']:
        raise SystemExit('the entry arrays do not match the CRC their headers name (primary %08x/%08x, backup %08x/%08x)'
                         % (crc(pe), ph['table_crc'], crc(be), bh['table_crc']))
    if pe != be:
        raise SystemExit('primary and backup entry arrays differ: bring the tables in order first')
    entry = pe[BOOT_A * ESIZE:(BOOT_A + 1) * ESIZE]
    if name_of(entry) != 'boot_a' or le(entry, 32, 8) != BOOT_A_FIRST or le(entry, 40, 8) != BOOT_A_LAST:
        raise SystemExit('boot_a is not where it was read on 2026-11-27')
    attr = le(entry, 48, 8)
    if attr == MARKED:
        print('boot_a is already marked successful (0x%016x): nothing to do' % attr)
        raise SystemExit(3)
    if attr not in UNVERIFIED:
        raise SystemExit('boot_a carries 0x%016x: not one of the unverified states read on 2026-11-27' % attr)
    # ---- the change ------------------------------------------------------------------------------
    new_pe = bytearray(pe)
    new_pe[BOOT_A * ESIZE + 48:BOOT_A * ESIZE + 56] = struct.pack('<Q', MARKED)
    new_pe = bytes(new_pe)
    low, high = BOOT_A * ESIZE + 48, BOOT_A * ESIZE + 56      # boot_a's attribute
    changed = [i for i in range(n) if new_pe[i] != pe[i]]
    if not changed or any(i < low or i >= high for i in changed):
        raise SystemExit('internal error: bytes outside the attribute of boot_a changed')
    if low // B != 0:
        raise SystemExit('internal error: the attribute is not in the first block of the entry array')
    ph_block = bytearray(first[B:2 * B])
    ph_block[88:92] = struct.pack('<I', crc(new_pe))
    ph_block = with_header_crc(bytes(ph_block))
    bh_block = bytearray(last[7 * B:8 * B])
    bh_block[88:92] = struct.pack('<I', crc(new_pe))
    bh_block = with_header_crc(bytes(bh_block))
    if header(ph_block)['table_crc'] != crc(new_pe) or header(bh_block)['table_crc'] != crc(new_pe):
        raise SystemExit('internal error: the new headers do not verify')
    after_first = first[:B] + ph_block + new_pe[:B] + first[3 * B:]
    after_last = last[:3 * B] + new_pe[:B] + last[4 * B:7 * B] + bh_block
    # the eight-block images must differ from the old ones in exactly the four blocks
    if sum(after_first[i * B:(i + 1) * B] != first[i * B:(i + 1) * B] for i in range(8)) != 2 or \
       sum(after_last[i * B:(i + 1) * B] != last[i * B:(i + 1) * B] for i in range(8)) != 2:
        raise SystemExit('internal error: unexpected number of changed blocks')
    out.mkdir(parents=True, exist_ok=True)
    plan = dict(lun_blocks=BLOCKS, block_bytes=B, disk_guid=str(DISK_GUID), boot_a_entry=BOOT_A,
                attribute_before='0x%016x' % attr, attribute_after='0x%016x' % MARKED,
                writes=[   # in this order: the backup first, the primary last
                    dict(file='b-entries.bin', lba=BLOCKS - 5, data=new_pe[:B]),
                    dict(file='b-header.bin', lba=BLOCKS - 1, data=bh_block),
                    dict(file='p-entries.bin', lba=2, data=new_pe[:B]),
                    dict(file='p-header.bin', lba=1, data=ph_block)],
                before=[dict(file='first8.bin', lba=0, data=first), dict(file='last8.bin', lba=BLOCKS - 8, data=last)],
                after=[dict(file='after-first8.bin', lba=0, data=after_first),
                       dict(file='after-last8.bin', lba=BLOCKS - 8, data=after_last)])
    record = {k: [] for k in ('writes', 'before', 'after')}
    for kind in ('writes', 'before', 'after'):
        for item in plan[kind]:
            (out / item['file']).write_bytes(item['data'])
            record[kind].append(dict(file=item['file'], lba=item['lba'], bytes=len(item['data']),
                                     sha256=hashlib.sha256(item['data']).hexdigest()))
    plan2 = {k: v for k, v in plan.items() if k not in record}
    plan2.update(record)
    (out / 'plan.json').write_text(json.dumps(plan2, indent=2) + '\n')
    print('boot_a attribute 0x%016x -> 0x%016x; four blocks to write: %s' % (
        attr, MARKED, ', '.join('%s at LBA %d' % (w['file'], w['lba']) for w in plan2['writes'])))


main()
