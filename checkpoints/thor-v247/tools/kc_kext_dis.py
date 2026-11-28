#!/usr/bin/env python3.13
"""Disassemble a kext that is prelinked into a kernel image, with names for what it imports.

  kc_kext_dis.py <kernelcache.macho> <prelink.json> <unstripped kernel> <start> <end> [...]

The prelinked kexts carry no symbol table in the image. Their calls to the kernel go through stubs
(`adrp x16 / ldr x16, [x16, #off] / br x16`), and the prelink record names the target of every
binding slot it rewrote (`bindings[].new_site` -> `new_target`). The target is looked up in the
unstripped kernel's symbol table. Direct calls inside a kext are shown with the kext's offset.
"""
import bisect, json, re, struct, subprocess, sys

image, record, kern = sys.argv[1:4]
ranges = list(zip(sys.argv[4::2], sys.argv[5::2]))
rec = json.load(open(record))

nm = subprocess.check_output(["xcrun", "nm", "-n", kern], text=True, stderr=subprocess.DEVNULL)
syms = []
for line in nm.splitlines():
    p = line.split()
    if len(p) == 3 and re.fullmatch(r"[0-9a-f]+", p[0]):
        syms.append((int(p[0], 16), p[2]))
syms.sort()
addrs = [s[0] for s in syms]


def ksym(va):
    i = bisect.bisect_right(addrs, va) - 1
    if i < 0 or va - syms[i][0] > 0x20000:
        return None
    return syms[i][1] + ("+0x%x" % (va - syms[i][0]) if va != syms[i][0] else "")


slot_name = {}
for b in rec["bindings"]:
    name = ksym(b["new_target"]) if b["kind"] == "kernel" else None
    if name:
        slot_name[b["new_site"]] = name

data = open(image, "rb").read()


def va2off(va):
    ncmds = struct.unpack_from("<I", data, 16)[0]
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19:
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
            if vmaddr <= va < vmaddr + vmsize and va - vmaddr < filesize:
                return fileoff + va - vmaddr
        off += size
    return None


od = subprocess.check_output(["xcrun", "--find", "llvm-objdump"], text=True).strip()
ext = {}
for e in rec["extents"]:
    ext.setdefault(e["module"], {})[e["segment"]] = (e["va"], e["size"])


def kext_offset(va):
    out = []
    for m, segs in ext.items():
        for seg, (a, s) in segs.items():
            if a <= va < a + s:
                out.append("%s %s+%#x" % (m.rsplit(".", 1)[-1], seg, va - a))
    return " ".join(out)


for start, end in ranges:
    print("=== %#x .. %#x  %s" % (int(start, 16), int(end, 16), kext_offset(int(start, 16))))
    text = subprocess.run([od, "-D", "--arch=arm64", "--no-show-raw-insn", "--print-imm-hex",
                           "--start-address=" + start, "--stop-address=" + end, image],
                          capture_output=True, text=True).stdout
    page = {}
    for l in text.split("\n"):
        l = l.strip()
        m = re.match(r"([0-9a-f]+):\s+(.*)", l)
        if not m:
            continue
        va = int(m.group(1), 16)
        ins = re.sub(r"\s*<[^>]*>", "", m.group(2))
        note = ""
        p = ins.split(None, 1)
        arg = p[1].split(",")[0] if len(p) > 1 else ""
        if p and p[0] == "adrp":
            r, imm = p[1].split(", ")
            page[r] = int(imm, 16)
        elif p and p[0] in ("ldr", "ldrb", "ldrh", "ldrsw") and len(p) > 1:
            mm = re.match(r"(\w+), \[(\w+)(?:, (#?0x[0-9a-f]+|\d+))?\]", p[1])
            if mm and mm.group(2) in page:
                t = page[mm.group(2)] + (int(mm.group(3).lstrip("#"), 16) if mm.group(3) else 0)
                o = va2off(t)
                if o is not None and mm.group(1).startswith("x"):
                    q = struct.unpack_from("<Q", data, o)[0]
                    if t in slot_name:
                        note = "   ; slot %#x -> %s" % (t, slot_name[t])
                    elif o is not None:
                        s = ksym(q)
                        if s:
                            note = "   ; %#x = %s" % (t, s)
                elif o is not None and (mm.group(1).startswith("w")):
                    pass
        elif p and p[0] in ("add", "sub") and len(p) > 1 and arg in page and p[1].count(",") == 2:
            imm = p[1].split(", ")[2].lstrip("#")
            t = page[arg] + (int(imm, 16) if imm.startswith("0x") else int(imm))
            o = va2off(t)
            if o is not None:
                z = data.find(b"\0", o)
                s = data[o:z]
                if 0 < len(s) < 160 and all(32 <= c < 127 or c in (9, 10) for c in s):
                    note = "   ; %r" % s.decode()
        elif p and p[0] in ("bl", "b") and len(p) > 1 and p[1].startswith("0x"):
            t = int(p[1].split()[0], 16)
            if t in slot_name:
                note = "   ; %s" % slot_name[t]
            else:
                o = va2off(t)
                stub = None
                if o is not None:
                    w = struct.unpack_from("<3I", data, o)
                    if (w[0] & 0x9F000000) == 0x90000000 and (w[0] & 31) == 16 \
                            and w[1] & 0xFFC00000 == 0xF9400000 and (w[1] >> 5) & 31 == 16 and w[2] == 0xD61F0200:
                        imm = ((w[0] >> 5) & 0x7FFFF) << 2 | ((w[0] >> 29) & 3)
                        if imm & (1 << 20):
                            imm -= 1 << 21
                        slot = ((t & ~0xFFF) + (imm << 12)) + ((w[1] >> 10) & 0xFFF) * 8
                        stub = slot_name.get(slot)
                        if stub is None:
                            so = va2off(slot)
                            q = struct.unpack_from("<Q", data, so)[0] if so is not None else 0
                            stub = ksym(q)
                if stub:
                    note = "   ; %s" % stub
        print("%14x:  %-48s%s" % (va, ins, note))
