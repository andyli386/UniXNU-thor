#!/usr/bin/env python3.13
"""Objective-C method addresses of a dylib extracted from a dyld shared cache.

   objc_methods.py <extracted dylib> [--sel <file>] [class-pattern | selector-pattern]...

--sel <file> is the output of `ipsw dyld objc sel -i <image> <cache>` for the same image (lines "0xADDR: name"):
it names the selectors of this image.  Strings that lie inside the image are read from the extracted file.

Prints, sorted by address:  imp address   +/-[Class(Category) selector]   type encoding
Categories are listed as Class(Category).  With patterns only matching methods are printed (regular expressions).
"""
import bisect, glob, os, re, struct, sys

args = sys.argv[1:]
path = args[0]
sel_file = None
pats = []
i = 1
while i < len(args):
    if args[i] == '--sel':
        sel_file = args[i + 1]; i += 2
    else:
        pats.append(args[i]); i += 1

data = open(path, 'rb').read()
magic, = struct.unpack_from('<I', data, 0)
assert magic == 0xfeedfacf, hex(magic)
ncmds, = struct.unpack_from('<I', data, 16)
segs, sects = {}, {}
pos = 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from('<II', data, pos)
    if cmd == 0x19:      # LC_SEGMENT_64
        segname = data[pos + 8:pos + 24].rstrip(b'\0').decode()
        vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, pos + 24)
        segs[segname] = (vmaddr, vmsize, fileoff, filesize)
        nsects, = struct.unpack_from('<I', data, pos + 64)
        for k in range(nsects):
            o = pos + 72 + k * 80
            sectname = data[o:o + 16].rstrip(b'\0').decode()
            addr, ssize, soff = struct.unpack_from('<QQI', data, o + 32)
            sects[(segname, sectname)] = (addr, ssize, soff)
    pos += size

# selector names by address (the cache's selector strings are outside this image)
selnames = {}
if sel_file:
    for line in open(sel_file, encoding='utf-8', errors='replace'):
        m = re.match(r'(0x[0-9a-f]+):\s(.*)$', line.strip())
        if m:
            selnames[int(m.group(1), 16)] = m.group(2)

# text mappings of the cache's own files (main and .NN sub-caches): strings that lie outside this image
# but inside the shared cache's text region are read from there; DSC_MAIN names the main cache file.
import bisect, glob
ranges, starts, handles = [], [], {}
main = os.environ.get('DSC_MAIN')
if main:
    for path in sorted(glob.glob(main + '*')):
        if not re.fullmatch(re.escape(main) + r'(\.\d+)?', path):
            continue
        with open(path, 'rb') as f:
            hdr = f.read(32)
            if hdr[:10] != b'dyld_v1   ':
                continue
            mappingOffset, mappingCount = struct.unpack_from('<II', hdr, 16)
            for k in range(mappingCount):
                f.seek(mappingOffset + k * 64)
                address, size, fileOffset = struct.unpack('<QQQ', f.read(24))
                maxProt, = struct.unpack('<I', f.read(4))
                if size and maxProt & 4:
                    ranges.append((address, address + size, path, fileOffset))
    ranges.sort()
    starts = [r[0] for r in ranges]

def read(addr, n):
    for vmaddr, vmsize, fileoff, filesize in segs.values():
        if vmaddr <= addr and addr + n <= vmaddr + filesize:
            return data[fileoff + addr - vmaddr:fileoff + addr - vmaddr + n]
    if ranges:
        i = bisect.bisect_right(starts, addr) - 1
        if i >= 0:
            s, e, path, off = ranges[i]
            if s <= addr and addr + n <= e:
                f = handles.get(path)
                if f is None:
                    f = handles[path] = open(path, 'rb')
                f.seek(off + addr - s)
                return f.read(n)
    return None

def u64(addr):
    b = read(addr, 8)
    return struct.unpack('<Q', b)[0] if b else 0

def cstr(addr, limit=600):
    out = b''
    while addr and len(out) < limit:
        chunk = read(addr + len(out), 96)
        if not chunk:
            break
        if b'\0' in chunk:
            return (out + chunk).split(b'\0', 1)[0].decode('utf-8', 'replace')
        out += chunk
    return None

rows = []
MASK = 0x00007ffffffffff8

def selector(ptr_or_direct, direct):
    if direct:
        return selnames.get(ptr_or_direct) or cstr(ptr_or_direct) or ('@%#x' % ptr_or_direct)
    target = u64(ptr_or_direct)
    return selnames.get(target) or (cstr(target) if target else None) or ('@%#x' % target)

def methods(mlist, prefix, name):
    if not mlist:
        return
    hdr = read(mlist, 8)
    if not hdr:
        return
    entsize_flags, count = struct.unpack('<II', hdr)
    small = bool(entsize_flags & 0x80000000)
    direct = bool(entsize_flags & 0x40000000)
    size = entsize_flags & 0xFFFC
    if size < 12 or size > 64 or not (0 <= count <= 60000):
        return
    for i in range(count):
        e = mlist + 8 + i * size
        if small:
            b = read(e, 12)
            if not b:
                break
            nrel, trel, irel = struct.unpack('<iii', b)
            sel = selector(e + nrel, direct)
            imp = e + 8 + irel
            enc = cstr(e + 4 + trel) or ''
        else:
            b = read(e, 24)
            if not b:
                break
            nptr, tptr, imp = struct.unpack('<QQQ', b)
            sel = selector(nptr, False)
            enc = cstr(tptr) or ''
        rows.append((imp, prefix + '[' + name + ' ' + (sel or '?') + ']', enc))

def class_name(cptr):
    if not cptr:
        return None
    ro = u64(cptr + 32) & MASK
    return cstr(u64(ro + 24)) if ro else None

def do_class(cptr):
    name = class_name(cptr)
    if not name:
        return
    isa = u64(cptr)
    ro = u64(cptr + 32) & MASK
    methods(u64(ro + 32), '-', name)
    mro = (u64(isa + 32) & MASK) if isa else 0
    if mro:
        methods(u64(mro + 32), '+', name)

def do_category(cptr):
    # category_t: name, cls, instanceMethods, classMethods, protocols, instanceProperties, ...
    cname, cls, imethods, cmethods = (u64(cptr + 8 * k) for k in range(4))
    name, base = cstr(cname), class_name(cls)
    title = (base or '?') + '(' + (name or '?') + ')'
    methods(imethods, '-', title)
    methods(cmethods, '+', title)

for key, fn in ((('__DATA_CONST', '__objc_classlist'), do_class), (('__DATA', '__objc_classlist'), do_class),
                (('__DATA_CONST', '__objc_catlist'), do_category), (('__DATA', '__objc_catlist'), do_category)):
    if key in sects:
        addr, size, off = sects[key]
        for k in range(size // 8):
            fn(u64(addr + 8 * k))

rows.sort()
shown = 0
for imp, title, enc in rows:
    if pats and not any(re.search(p, title) for p in pats):
        continue
    print('%016x  %s   %s' % (imp, title, enc))
    shown += 1
print('%d methods listed of %d' % (shown, len(rows)), file=sys.stderr)
