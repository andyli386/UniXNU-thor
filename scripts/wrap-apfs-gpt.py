#!/usr/bin/env python3
"""Wrap a bare APFS container in a minimal GPT (protective MBR + GPT + one
APFS-type partition), so the PongUfs transport sees the "EFI PART" signature
at byte 512 it requires for the OS and Data SHNGROOT windows.

The container bytes are copied verbatim into the partition; nothing inside
the APFS (volume UUIDs, checksums) is touched.
"""
import argparse, hashlib, struct, sys, uuid, zlib
from pathlib import Path

LBA = 512
PART_OFFSET_LBA = 2048          # 1 MiB alignment for the partition start
APFS_TYPE = uuid.UUID('7c3457ef-0000-11aa-aa11-00306543ecac')


def gpt_header(disk_last_lba, part_first, part_last, entries_lba, entry_count=128):
    h = bytearray(92)
    h[0:8] = b'EFI PART'
    struct.pack_into('<II', h, 8, 0x00010000, 92)          # rev, header size
    struct.pack_into('<I', h, 84, 0)                        # crc placeholder
    struct.pack_into('<Q', h, 72, disk_last_lba)            # backup lba
    struct.pack_into('<Q', h, 24, entries_lba)              # entries lba
    struct.pack_into('<I', h, 80, entry_count)              # entry count
    struct.pack_into('<I', h, 84, 0)
    struct.pack_into('<I', h, 16, disk_last_lba)            # last usable lba... fixed below
    return h


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--part-guid', default=None, help='partition GUID (hex/uuid string)')
    a = p.parse_args()

    src = a.input
    size = src.stat().st_size
    assert size % LBA == 0, 'container must be 512-byte aligned'
    part_lba_count = size // LBA
    first = PART_OFFSET_LBA
    last = first + part_lba_count - 1
    # disk: [0, first) reserved, partition, 33 LBA of backup GPT at the end
    disk_last_lba = last + 33
    total = (disk_last_lba + 1) * LBA

    part_guid = uuid.UUID(a.part_guid) if a.part_guid else uuid.uuid4()

    # protective MBR: one entry of type 0xEE spanning the disk
    mbr = bytearray(LBA)
    mbr[510:512] = b'\x55\xaa'
    struct.pack_into('<B3sB3sBI', mbr, 446, 0, b'\x00' * 3, 0xEE, b'\x00' * 3, 1, min(disk_last_lba, 0xFFFFFFFF))

    # partition entry
    entry = bytearray(128)
    entry[0:16] = APFS_TYPE.bytes_le
    entry[16:32] = part_guid.bytes_le
    struct.pack_into('<QQQ', entry, 32, first, last, 0)
    entry[72:72 + len(b'UnixNU OS/Data') ] = b'UnixNU OS/Data'
    entries = bytearray(128 * 128)
    entries[0:128] = entry

    entries_crc = zlib.crc32(bytes(entries)) & 0xFFFFFFFF

    def header(current_lba, backup_lba, entries_lba):
        h = bytearray(92)
        h[0:8] = b'EFI PART'
        struct.pack_into('<II', h, 8, 0x00010000, 92)
        struct.pack_into('<Q', h, 16, disk_last_lba - 33)   # last usable
        struct.pack_into('<Q', h, 24, current_lba)
        struct.pack_into('<Q', h, 32, backup_lba)
        struct.pack_into('<Q', h, 40, first)                # partition entry first
        struct.pack_into('<Q', h, 48, last)                 # partition entry last
        struct.pack_into('<I', h, 72, disk_last_lba)        # disk guid material (reused) -> keep simple
        struct.pack_into('<III', h, 72, 0, 0, 0)
        h[72:88] = uuid.uuid5(uuid.NAMESPACE_OID, str(part_guid)).bytes_le  # disk guid, deterministic
        struct.pack_into('<I', h, 80, 128)
        struct.pack_into('<I', h, 84, 92)
        struct.pack_into('<I', h, 88, entries_crc)
        crc = zlib.crc32(bytes(h)) & 0xFFFFFFFF
        struct.pack_into('<I', h, 16, crc)
        return h

    primary_header = header(1, disk_last_lba, 2)
    backup_header = header(disk_last_lba, 1, disk_last_lba - 32)

    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('wb') as out:
        out.write(mbr)
        out.write(bytes(primary_header))
        out.write(bytes(entries))
        out.seek(first * LBA)
        copied = 0
        with src.open('rb') as f:
            while chunk := f.read(8 << 20):
                out.write(chunk)
                copied += len(chunk)
        assert copied == size, 'short copy'
        out.truncate(total)

    print(f'{a.output}: container {size} at lba {first}, disk {total} bytes, '
          f'part-guid {part_guid}')


if __name__ == '__main__':
    main()
