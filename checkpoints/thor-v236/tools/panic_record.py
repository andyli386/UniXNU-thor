"""Decode a THOR WITNESS panic record and symbolicate it with the kernel that wrote it.

  panic_record.py <PongPanic-vNNN-N.bin> [--kernel <mach.development.pong.unstripped of THAT version>]
                 [--raw] [--all]

Record layout (thor_witness_slot_store, kern_exec.c):
   0  "THORPAN1"            8  u32 record bytes         12 u32 stock bytes
  16  u64 mach_absolute_time 24 u32 extra bytes          28 u32 layout version (1)
  64  the kernel's panic log, then the THOR WITNESS extras; the rest of the 256 KiB is zero.

With --kernel, every kernel address in the fault line and in the stack scan is also resolved
against that kernel's symbol table and function starts (the kernel's own names are printed as
stored; the two must agree).
"""
import argparse
import bisect
import json
import pathlib
import re
import struct
import subprocess

HEX = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{12,16}\b")
STACK = re.compile(r"^\s*\+([0-9a-f]{4,6}): ([0-9a-f]{16}) (.*)$")


def kernel_symbols(path):
    out = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", "-n", path], text=True, stderr=subprocess.DEVNULL)
    rows = []
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3:
            try:
                rows.append((int(p[0], 16), p[2]))
            except ValueError:
                pass
    rows = [r for r in rows if "payload" not in r[1]]
    rows.sort()
    return rows


def describe(rows, value):
    i = bisect.bisect_right([r[0] for r in rows], value) - 1
    if i < 0:
        return "%x (no symbol below)" % value
    return "%s+0x%x%s" % (rows[i][1], value - rows[i][0], "" if value - rows[i][0] < 0x2000 else " (far above the symbol)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--kernel")
    ap.add_argument("--raw", action="store_true", help="print the stock panic log as stored")
    ap.add_argument("--all", action="store_true", help="print the extras as stored, even when symbolicated")
    a = ap.parse_args()
    data = pathlib.Path(a.file).read_bytes()
    if data[:8] != b"THORPAN1":
        sys_exit = "not a panic record (first 8 bytes %r)" % data[:8]
        print(sys_exit)
        return
    total, stock, when, extra, version = struct.unpack_from("<IIQII", data, 8)
    print("panic record: %d bytes, stock %d, extras %d, layout %d, mach_absolute_time %#x (%.3f s of the kernel clock)" % (
        total, stock, extra, version, when, when / 19.2e6))
    text = data[64:64 + stock].decode("utf-8", "replace")
    extras = data[64 + stock:64 + stock + extra].decode("utf-8", "replace")
    print("=" * 100)
    print("KERNEL PANIC LOG")
    print("=" * 100)
    print(text.rstrip())
    print("=" * 100)
    print("THOR WITNESS EXTRAS")
    print("=" * 100)
    rows = kernel_symbols(a.kernel) if a.kernel else None
    if rows is None or a.all:
        print(extras.rstrip())
    if rows is not None:
        print("=" * 100)
        print("same addresses against %s" % pathlib.Path(a.kernel).name)
        print("=" * 100)
        for line in extras.splitlines():
            m = STACK.match(line)
            if m:
                print("  stack+%s: %s   %s" % (m.group(1), describe(rows, int(m.group(2), 16)), m.group(3)))
            elif line.lstrip().startswith("fault: pc=") or line.lstrip().startswith("pc symbol") or " user: " in line:
                print("  " + line.strip())
                for h in re.findall(r"\b[0-9a-f]{16}\b", line):
                    v = int(h, 16)
                    if 0xfffffff009a74000 <= v < 0xfffffff00a3d4000 and rows:
                        print("      %s -> %s" % (h, describe(rows, v)))


if __name__ == "__main__":
    main()
