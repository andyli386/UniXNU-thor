#!/usr/bin/env python3
"""Read validated Thor journal records from a directory produced by Recovery capture."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import struct
import uuid
import zlib

ROLES = ("runningboardd", "SpringBoard", "backboardd")
SLOT_BYTES = 512
SLOT_COUNT = 128
RECOVERY_PATHS = [f"{directory}/ThorStall-{role}.bin"
                  for directory in ("logs", "mobile/Library/Logs") for role in ROLES]

RECOVERY_PATHS += [s.replace("/ThorStall-", "/ThorStall-v220-").replace("/ThorSwipeStack-", "/ThorSwipeStack-v220-") for s in RECOVERY_PATHS]

def read_events(path, role, target_boot=None):
    if path.stat().st_size > SLOT_BYTES * SLOT_COUNT:
        raise ValueError(f"oversized journal: {path}")
    data = path.read_bytes()
    records = []
    invalid = []
    for index in range(len(data) // SLOT_BYTES):
        block = data[index*SLOT_BYTES:(index+1)*SLOT_BYTES]
        if block == bytes(SLOT_BYTES):
            continue
        seq, instance, pid, role_id, length = struct.unpack_from("<QQIBxH", block, 8)
        try:
            boot = str(uuid.UUID(block[32:68].decode("ascii"))).upper()
            if (block[:4] != b"TSJ1" or seq < 1 or (seq-1) % SLOT_COUNT != index or
                    not 0 < instance <= seq or not pid or role_id >= len(ROLES) or
                    ROLES[role_id] != role or not 1 <= length <= 383 or
                    struct.unpack_from("<I", block, 508)[0] != zlib.crc32(block[:508])):
                raise ValueError("invalid framing")
            text = block[68:68+length].decode("utf-8")
            if not text.endswith("\n") or "\n" in text[:-1] or "\x00" in text:
                raise ValueError("invalid event")
        except (UnicodeError, ValueError):
            invalid.append(index)
            continue
        if not target_boot or boot == target_boot:
            records.append({"boot": boot, "role": role, "pid": pid, "instance": instance,
                            "seq": seq, "line": text[:-1], "slot": index})
    return ({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
             "partial_tail_bytes": len(data) % SLOT_BYTES, "invalid_slots": invalid},
            sorted(records, key=lambda r: r["seq"]))


def role_history(records):
    """Expand kernel role transitions kept together in CRC-checked journal slots."""
    roles = {}
    gaps = {}
    for row in records:
        line = row['line']
        if row['role'] != 'runningboardd' or not line.startswith('thor-role-v227 '):
            continue
        header = re.fullmatch(r'thor-role-v227 first=([1-9][0-9]*) lost=([0-9]+) events=(.+)', line)
        if not header:
            raise ValueError('invalid role receipt')
        first, lost = map(int, header.groups()[:2])
        batch = header[3].split(',')
        if not 0 <= lost < first or not 1 <= len(batch) <= 4 or first + len(batch) > 2**64:
            raise ValueError('invalid role receipt sequence')
        owner = (row['boot'], row['pid'], row['instance'])
        if lost:
            gaps[(*owner, first)] = lost
        for index, item in enumerate(batch):
            fields = item.split('/')
            if len(fields) != 4:
                raise ValueError('invalid role event')
            at, pid, fg, name = fields
            if not (at.isascii() and at.isdigit() and 0 < int(at) < 2**64 and
                    pid.isascii() and pid.isdigit() and 0 < int(pid) < 2**31 and
                    fg in ('0', '1') and 1 <= len(name) <= 31 and
                    all(33 <= ord(ch) <= 126 and ch not in '/,' for ch in name)):
                raise ValueError('invalid role event content')
            key = (*owner, first + index)
            event = dict(boot=owner[0], writer_pid=owner[1], instance=owner[2], serial=key[-1],
                         at_us=int(at), pid=int(pid), foreground=bool(int(fg)), name=name)
            if key in roles and roles[key] != event:
                raise ValueError('conflicting copies of role event')
            roles[key] = event
    ordered = sorted(roles)
    discontinuities = []
    for a,b in zip(ordered,ordered[1:]):
        if a[:3]==b[:3] and b[-1] > a[-1]+1:
            discontinuities.append(dict(writer_pid=b[1],instance=b[2],after=a[-1],before=b[-1]))
    return dict(events=[roles[k] for k in ordered],
                writer_overflows=[dict(writer_pid=k[1],instance=k[2],before=k[3],lost=gaps[k]) for k in sorted(gaps)],
                sequence_gaps=discontinuities)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Recovery capture directory (contains Data), or Data directory")
    parser.add_argument("--boot", type=uuid.UUID, help="Darwin kern.bootsessionuuid")
    parser.add_argument("--role-history", action="store_true", help="include application foreground/background events")
    args = parser.parse_args()
    if args.role_history and not args.boot:
        parser.error("--role-history requires --boot")
    root = args.capture / "Data" if (args.capture / "Data").is_dir() else args.capture
    files, records = {}, []
    for rel in RECOVERY_PATHS:
        path = root / rel
        if not path.is_file():
            continue
        role = path.stem.removeprefix("ThorStall-").removeprefix("v220-")
        files[rel], events = read_events(path, role, str(args.boot).upper() if args.boot else None)
        for event in events:
            event["file"] = rel
            records.append(event)
    result = {"files": files, "events": records}
    if args.role_history:
        result["role_history"] = role_history(records)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
