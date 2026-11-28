#!/usr/bin/env python3.13
"""Explain the calls in a stripped app binary: stub addresses -> symbol names, selector and class
references -> their strings.

  macho_calls.py <macho> <function start> <function end> [...]
                    annotated disassembly (llvm-objdump) of each range: every bl/blr target in a stub
                    section is replaced by its symbol, adrp/add/ldr pairs that load a selector or
                    class reference are annotated with the string they name.
  macho_calls.py <macho> --stubs          list every stub with its symbol
"""
import bisect, re, struct, subprocess, sys
from pathlib import Path

path = sys.argv[1]
data = Path(path).read_bytes()
magic, _ct, _cs, ft, ncmds, sizeofcmds, _fl, _r = struct.unpack_from("<IiiIIIII", data)
assert magic == 0xFEEDFACF
segs, sects, symtab, dysym = [], [], None, None
off = 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from("<II", data, off)
    if cmd == 0x19:
        segname = data[off + 8:off + 24].split(b"\0")[0].decode()
        vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
        segs.append((segname, vmaddr, vmsize, fileoff, filesize))
        so = off + 72
        for _s in range(struct.unpack_from("<I", data, off + 64)[0]):
            sname = data[so:so + 16].split(b"\0")[0].decode()
            addr, ssize, soff = struct.unpack_from("<QQI", data, so + 32)
            flags, r1, r2 = struct.unpack_from("<III", data, so + 64)
            sects.append((segname, sname, addr, ssize, soff, flags, r1, r2))
            so += 80
    elif cmd == 0x2:
        symtab = struct.unpack_from("<IIII", data, off + 8)
    elif cmd == 0xB:
        dysym = struct.unpack_from("<18I", data, off + 8)       # indirectsymoff is field 13, nindirectsyms field 14
    off += size


def va2off(va):
    for _n, vmaddr, vmsize, fileoff, filesize in segs:
        if vmaddr <= va < vmaddr + vmsize and va - vmaddr < filesize:
            return fileoff + va - vmaddr
    return None


BASE = min(va for _n, va, _s, _f, _fs in segs if _n != "__PAGEZERO")   # the image's load address
symoff, nsyms, stroff, strsize = symtab
names = []
for i in range(nsyms):
    strx, ty, sect, desc, value = struct.unpack_from("<IBBHQ", data, symoff + 16 * i)
    z = data.find(b"\0", stroff + strx)
    names.append(data[stroff + strx:z].decode("latin1"))

indirect = []
if dysym:
    indirect = struct.unpack_from("<%dI" % dysym[13], data, dysym[12])


def stub_symbol(va):
    """The symbol a stub at va jumps to, for S_SYMBOL_STUBS / S_LAZY_SYMBOL_STUBS sections."""
    for segname, sname, addr, ssize, soff, flags, r1, r2 in sects:
        if addr <= va < addr + ssize and (flags & 0xFF) in (0x8, 0x0) and r2:
            size = r2
            idx = r1 + (va - addr) // size
            if idx < len(indirect):
                return names[indirect[idx]] if indirect[idx] < len(names) else None
    return None


def cstr(va):
    o = va2off(va)
    if o is None:
        return None
    z = data.find(b"\0", o)
    return data[o:z].decode("latin1")


S_ZEROFILL = 0x1
refs = []
for segname, sname, addr, ssize, soff, flags, r1, r2 in sects:
    if ssize and sname in ("__objc_selrefs", "__objc_classrefs", "__objc_superrefs", "__objc_protorefs", "__got", "__la_symbol_ptr", "__nl_symbol_ptr") and (flags & 0xFF) != 0x1:
        refs.append((addr, ssize, soff, sname))
refs.sort()
ref_starts = [r[0] for r in refs]


def sname_of(va):
    for addr, ssize, soff, sname, in refs:
        if addr <= va < addr + ssize:
            return sname
    return ""


def ref_info(va):
    """What a pointer at va names: a selector or class string, or a bound symbol."""
    for addr, ssize, soff, sname, in refs:
        if addr <= va < addr + ssize:
            o = soff + va - addr
            if o + 8 > len(data):
                return None
            value = struct.unpack_from("<Q", data, o)[0]
            if value >> 36 and (value & 0xFFFFFFFFF):          # chained fix-up pointer: target in the low 36 bits
                value &= 0xFFFFFFFFF
            if value and value < BASE:                            # chained fix-ups store targets as offsets from the image
                value += BASE
            if value and va2off(value) is not None:
                s = cstr(value)
                if s is not None and s.isprintable() and len(s) < 200:
                    return "%s %r" % (sname.replace("__objc_", "").replace("refs", "").strip("_") or "ptr", s)
            if value:
                i = bisect.bisect_right([0], 1)  # placeholder: symbol lookup below
            return None
    return None


# pointers left unbound at build time carry their symbol index in the indirect table (BIND sections)
bind_names = {}
for segname, sname, addr, ssize, soff, flags, r1, r2 in sects:
    if ssize and (flags & 0xFF) != 0x1 and (flags & 0xFF) in (0x6, 0x7, 0x9, 0xA) and r2 == 0:
        for k in range(ssize // 8):
            idx = r1 + k
            if idx < len(indirect) and indirect[idx] < len(names):
                bind_names[addr + 8 * k] = names[indirect[idx]]

if sys.argv[2] == "--stubs":
    for segname, sname, addr, ssize, soff, flags, r1, r2 in sects:
        if sname in ("__stubs", "__stub_helper") or ((flags & 0xFF) in (0x8, 0x0) and r2):
            for k in range(ssize // r2):
                print("%#x %s" % (addr + k * r2, names[indirect[r1 + k]] if r1 + k < len(indirect) else "?"))
    sys.exit(0)

od = subprocess.check_output(["xcrun", "--find", "llvm-objdump"], text=True).strip()
ranges = [(int(a, 16), int(b, 16)) for a, b in zip(sys.argv[2::2], sys.argv[3::2])]
for start, end in ranges:
    print("=== %#x..%#x" % (start, end))
    text = subprocess.run([od, "-d", "--arch=arm64", "--no-show-raw-insn", "--print-imm-hex",
                           "--start-address=%#x" % start, "--stop-address=%#x" % end, path], capture_output=True, text=True).stdout
    adrp = {}
    for l in text.split("\n"):
        l = l.strip()
        if not l or ":" not in l:
            continue
        head, ins = l.split(":", 1)
        ins = ins.strip()
        if not re.fullmatch(r"[0-9a-f]+", head.strip()):      # the file header line also holds a colon
            continue
        va = int(head, 16)
        note = ""
        p = ins.split(None, 1)
        if p and p[0] == "adrp":
            r, imm = p[1].split(", ")
            adrp[r] = int(imm.split(" <")[0], 16)
        elif p and p[0] == "add" and len(p) > 1:
            a = p[1].split(", ")
            if len(a) == 3 and a[0] == a[1] and a[0] in adrp and a[2].startswith("#"):
                adrp[a[0]] += int(a[2][1:], 16)
        elif p and p[0] in ("ldr", "ldur") and len(p) > 1:
            # "x1, [x8, #0x998]" or "x8, [x8, #0x410]": a load through a page register names a pointer
            m = re.match(r"(\w+), \[(\w+)(?:, (#0x[0-9a-f]+|\d+))?\]", p[1])
            if m and m.group(2) in adrp:
                target = adrp[m.group(2)] + (int(m.group(3)[1:], 16) if m.group(3) else 0)
                info = bind_names.get(target) if sname_of(target) in ("__got", "__la_symbol_ptr", "__nl_symbol_ptr") else ref_info(target)
                if info:
                    note = "   ; " + info
                elif target in bind_names:
                    note = "   ; " + bind_names[target]
                adrp[m.group(1)] = target
        if p and p[0] in ("bl", "b") and len(p) > 1 and p[1].startswith("0x"):
            s = stub_symbol(int(p[1].split(" <")[0], 16))
            if s:
                note = "   ; " + s
        ins = ins.split(" <")[0]
        print("%8x:  %-52s%s" % (va - 0x100000000, ins, note))
