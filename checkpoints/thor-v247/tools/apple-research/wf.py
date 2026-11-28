#!/usr/bin/env python3.13
"""Annotated disassembly of an on-dist Mach-O (wifid): ObjC method names, objc_msgSend stubs -> selector, selrefs, cfstrings,
GOT symbols, block invokes.   wf.py <binary> <out-prefix>   writes <prefix>.dis (annotated disassembly) and <prefix>.methods
"""
import re, struct, subprocess, sys, bisect
sys.path.insert(0, '/tmp/claude-501/cont')
from macho import Macho

path, prefix = sys.argv[1], sys.argv[2]
m = Macho(path)
data = m.data
base = m.base
sect = {(s[0], s[1]): (s[2], s[3], s[4]) for s in m.sects}

# ---- chained fixups: imports
imports = []
off = 32
for _ in range(m.ncmds):
    cmd, size = struct.unpack_from('<II', data, off)
    if cmd == 0x80000034:
        doff, dsize = struct.unpack_from('<II', data, off + 8)
        ver, starts_off, imports_off, symbols_off, imports_count, imports_format, symbols_format = struct.unpack_from('<7I', data, doff)
        for i in range(imports_count):
            v, = struct.unpack_from('<I', data, doff + imports_off + i * 4)
            libord = v & 0xFF
            nameoff = v >> 9
            z = data.find(b'\0', doff + symbols_off + nameoff)
            imports.append(data[doff + symbols_off + nameoff:z].decode('latin1'))
    off += size

def ptr(va):
    """(target, importname) of the chained-fixup pointer at va; target None for binds, (None, None) if unreadable"""
    o = m.off(va)
    if o is None or o + 8 > len(data):
        return None, None
    v, = struct.unpack_from('<Q', data, o)
    if v >> 63:
        ordinal = v & 0xFFFFFF
        if ordinal == 0xFFFFFF or ordinal >= len(imports):
            return None, None
        return None, imports[ordinal]
    t = v & 0xFFFFFFFFF
    if t and t < base:
        t += base
    return t, None

# ---- ObjC metadata
def cstr(va):
    if not va:
        return None
    return m.cstr(va)

def rd32(va):
    o = m.off(va)
    return struct.unpack_from('<i', data, o)[0] if o is not None else None

methods = {}   # imp -> 'name'
classes = {}   # class address -> name
metaclasses = {}

def sel_of(field, direct):
    rel = rd32(field)
    if rel is None:
        return None
    if direct:
        return cstr(field + rel)
    t, _ = ptr(field + rel)
    return cstr(t) if t else None

def read_methods(mlist, prefix, name):
    if not mlist:
        return
    o = m.off(mlist)
    if o is None:
        return
    entsize_flags, count = struct.unpack_from('<II', data, o)
    small = bool(entsize_flags & 0x80000000)
    direct = bool(entsize_flags & 0x40000000)
    size = entsize_flags & 0xFFFC
    if size < 12 or size > 64 or not (0 <= count <= 60000):
        return
    for i in range(count):
        e = mlist + 8 + i * size
        if small:
            sel = sel_of(e, direct)
            irel = rd32(e + 8)
            imp = e + 8 + irel if irel is not None else None
        else:
            p, _ = ptr(e)
            sel = cstr(p) if p else None
            imp, _ = ptr(e + 16)
        if imp:
            methods[imp] = '%s[%s %s]' % (prefix, name, sel or '?')

def class_name(cptr):
    if not cptr:
        return None
    ro, _ = ptr(cptr + 32)
    if not ro:
        return None
    ro &= ~7
    return cstr(ptr(ro + 24)[0])

def do_class(cptr):
    name = class_name(cptr)
    if not name:
        return
    classes[cptr] = name
    ro, _ = ptr(cptr + 32)
    ro &= ~7
    read_methods(ptr(ro + 32)[0], '-', name)
    isa, _ = ptr(cptr)
    if isa:
        metaclasses[isa] = name
        mro, _ = ptr(isa + 32)
        if mro:
            read_methods(ptr((mro & ~7) + 32)[0], '+', name)

def do_category(cptr):
    cname, _ = ptr(cptr)
    cls, _ = ptr(cptr + 8)
    name = cstr(cname)
    b = class_name(cls)
    title = (b or '?') + '(' + (name or '?') + ')'
    read_methods(ptr(cptr + 16)[0], '-', title)
    read_methods(ptr(cptr + 24)[0], '+', title)

for key, fn in ((('__DATA_CONST', '__objc_classlist'), do_class), (('__DATA', '__objc_classlist'), do_class),
                (('__DATA_CONST', '__objc_catlist'), do_category), (('__DATA', '__objc_catlist'), do_category)):
    if key in sect:
        addr, size, _ = sect[key]
        for k in range(size // 8):
            p, _ = ptr(addr + 8 * k)
            if p:
                fn(p)

# ---- objc stubs: adrp x1, selref; ldr x1, [x1, off]; adrp x6, got; ldr x6, [x6, off]; br x6
def dec_adrp(w, pc):
    if (w & 0x9F000000) != 0x90000000:
        return None
    rd = w & 0x1F
    immlo = (w >> 29) & 3
    immhi = (w >> 5) & 0x7FFFF
    imm = (immhi << 2) | immlo
    if imm & (1 << 20):
        imm -= 1 << 21
    return rd, ((pc >> 12) << 12) + (imm << 12)

def dec_ldr64(w):
    if (w & 0xFFC00000) != 0xF9400000:
        return None
    imm12 = (w >> 10) & 0xFFF
    rn = (w >> 5) & 0x1F
    rt = w & 0x1F
    return rt, rn, imm12 * 8

stub_sel = {}
if ('__TEXT', '__objc_stubs') in sect:
    addr, size, _ = sect[('__TEXT', '__objc_stubs')]
    for a in range(addr, addr + size, 32):
        o = m.off(a)
        w1, w2, w3, w4 = struct.unpack_from('<4I', data, o)
        d1 = dec_adrp(w1, a)
        l1 = dec_ldr64(w2)
        d2 = dec_adrp(w3, a + 8)
        l2 = dec_ldr64(w4)
        if not (d1 and l1 and d2 and l2):
            continue
        selref = d1[1] + l1[2]
        t, _ = ptr(selref)
        s = cstr(t) if t else None
        got = d2[1] + l2[2]
        _, sym = ptr(got)
        stub_sel[a] = (s or '?%#x' % selref, sym or '?')

# ---- function starts and names
starts = m.starts
funcname = {}
for a in starts:
    funcname[a] = None
for imp, n in methods.items():
    funcname[imp] = n
# stubs
stub_names = m._stub
symval = {}
for nm in m.names:
    pass
symtab_off, nsyms, stroff, strsize = m.symtab
for i in range(nsyms):
    strx, ty, sect_, desc, value = struct.unpack_from('<IBBHQ', data, symtab_off + 16 * i)
    z = data.find(b'\0', stroff + strx)
    nm = data[stroff + strx:z].decode('latin1')
    if nm and value and not (ty & 0x0e) == 0x0e and nm.startswith('__OBJC_IVAR'):
        symval[value] = nm

def in_sect(t, *keys):
    for k in keys:
        if k in sect:
            a_, s_, _ = sect[k]
            if a_ <= t < a_ + s_:
                return True
    return False

# ---- disassembly
text = subprocess.run(['xcrun', 'llvm-objdump', '--macho', '-d', '--no-show-raw-insn', path], capture_output=True, text=True).stdout
LINE = re.compile(r'^\s*([0-9a-f]+):\t(.*)$')
pages = {}
out = []
nfunc = 0
pending = {}   # addr of 'adrp+add' target function -> last computed (to name str x8 into block)
cur = None
for line in text.splitlines():
    mm = LINE.match(line)
    if not mm:
        continue
    addr = int(mm.group(1), 16)
    body = mm.group(2)
    # function header
    i = bisect.bisect_right(starts, addr) - 1
    if i >= 0 and starts[i] == addr:
        cur = addr
        name = funcname.get(addr)
        hdr = 'sub_%x' % addr if name is None else name
        out.append('')
        out.append('%s:' % hdr)
        nfunc += 1
    # split comment
    if ';' in body:
        ins, comment = body.split(';', 1)
    else:
        ins, comment = body, ''
    ins = ins.strip()
    parts = ins.split(None, 1)
    op = parts[0] if parts else ''
    ops = parts[1].strip() if len(parts) > 1 else ''
    note = ''
    g = re.match(r'^(x\d+), (\d+) ; (0x[0-9a-f]+)$', ops)   # llvm prints "adrp x8, 563 ; 0x100235000" -> we split on ';' so ops is "x8, 563 "
    if op == 'adrp':
        g2 = re.match(r'^(x\d+), (\d+)$', ops)
        g3 = re.match(r'^(x\d+), (0x[0-9a-f]+)$', ops)
        if g3:
            pages[g3.group(1)] = int(g3.group(2), 16)
        elif g2:
            pages[g2.group(1)] = (addr & ~0xFFF) + (int(g2.group(2)) << 12)
    elif op == 'add':
        g = re.match(r'^(x\d+), (x\d+), #(0x[0-9a-f]+|\d+)$', ops)
        if g and g.group(2) in pages and isinstance(pages[g.group(2)], tuple):
            pages.pop(g.group(2), None)
            pages.pop(g.group(1), None)
            continue
        if g and g.group(2) in pages:
            t = pages[g.group(2)] + int(g.group(3), 0)
            pages[g.group(1)] = t   # keep the page+off as a "page" value so ldr [x, #off] still works
            pages.pop(g.group(2), None) if g.group(1) != g.group(2) else None
            # name function pointers (block invokes)
            if t in funcname:
                note = '-> %s' % (funcname[t] or ('sub_%x' % t))
                pending[t] = True
            elif t in classes:
                note = '-> class %s' % classes[t]
            elif t in metaclasses:
                note = '-> metaclass %s' % metaclasses[t]
            elif in_sect(t, ('__DATA_CONST', '__cfstring')):
                p, _ = ptr(t + 16)
                s = m.cstr(p) if p else None
                note = '= CFSTR(%r)' % (s or '?')[:160] if s is not None else '= cfstring %#x' % t
            elif in_sect(t, ('__DATA_CONST', '__objc_intobj'), ('__DATA_CONST', '__objc_arrayobj'), ('__DATA_CONST', '__objc_dictobj'), ('__DATA_CONST', '__objc_floatobj')):
                note = '= objc literal object %#x' % t
            elif in_sect(t, ('__TEXT', '__cstring'), ('__TEXT', '__oslogstring'), ('__TEXT', '__ustring'), ('__TEXT', '__objc_methname'), ('__TEXT', '__dlopen_cstrs')):
                s = m.cstr(t)
                if s is not None and len(s) > 1:
                    note = '= %r' % s[:160]
            elif t in symval:
                note = '= %s' % symval[t]
            elif ('__DATA_CONST', '__cfstring') in sect:
                ca, cs, _ = sect[('__DATA_CONST', '__cfstring')]
                if ca <= t < ca + cs:
                    note = '= cfstring %#x' % t
        else:
            for r in list(pages):
                pass
            if g:
                pages.pop(g.group(1), None)
    elif op in ('ldr', 'ldur', 'ldrb', 'ldrh', 'ldrsw', 'str'):
        g = re.match(r'^([wxdq]\d+), \[(x\d+)(?:, #(0x[0-9a-f]+|\d+))?\]$', ops)
        if g and g.group(2) in pages and op not in ('str',) and isinstance(pages[g.group(2)], tuple):
            note = ''
            if op == 'ldr' and not g.group(3):
                note = '= value of %s' % pages[g.group(2)][1]
            pages.pop(g.group(2), None)
            pages.pop(g.group(1), None)
            if note:
                out.append('%8x:  %-48s ; %s' % (addr, ins, note))
            continue
        if g and g.group(2) in pages and op not in ('str',):
            target = pages[g.group(2)] + (int(g.group(3), 0) if g.group(3) else 0)
            t, sym = ptr(target)
            note = ''
            if op == 'ldr' and re.match(r'^x', g.group(1)):
                if isinstance(pages.get(g.group(2)), tuple):
                    note = '= value of %s' % pages[g.group(2)][1]
                elif sym:
                    note = '= GOT %s' % sym
                    pages[g.group(1)] = ('GOT', sym)
                    if g.group(1) != g.group(2):
                        pages.pop(g.group(2), None)
                    if note:
                        out.append('%8x:  %-48s ; %s' % (addr, ins, note))
                    continue
                    if sym.startswith('_OBJC_CLASS_$_'):
                        note += ' [%s]' % classes.get(t, '?')
                elif t is not None:
                    s = m.cstr(t)
                    if s is not None:
                        # selref or cstring pointer
                        sec = [(k) for k, (a, sz, _) in sect.items() if a <= target < a + sz]
                        if ('__DATA', '__objc_selrefs') in sec:
                            note = 'SEL %r' % s[:120]
                        else:
                            note = '-> %r' % s[:160]
                    elif t in funcname or t in classes:
                        note = '-> %s' % (funcname.get(t) or classes.get(t) or 'sub_%x' % t)
                    elif ('__DATA_CONST', '__cfstring') in sect:
                        ca, cs, _ = sect[('__DATA_CONST', '__cfstring')]
                        if ca <= t < ca + cs:
                            p, _ = ptr(t + 16)
                            s = m.cstr(p) if p else None
                            if s is not None:
                                note = '-> CFSTR(%r)' % s[:160]
                        elif t in methods:
                            note = '-> %s' % methods[t]
            if g.group(1) != g.group(2):
                pages.pop(g.group(2), None)
            if note:
                note = note
        elif g:
            pages.pop(g.group(1), None)
    elif op in ('bl', 'b') or op.startswith('b.'):
        g = re.match(r'^(0x[0-9a-f]+)$', ops)
        if g:
            t = int(g.group(1), 16)
            if t in stub_sel:
                note = '%s$%s' % ((stub_sel[t][1] or 'objc_msgSend').replace('_objc_', ''), stub_sel[t][0])
            elif t in stub_names and stub_names[t]:
                note = stub_names[t]
            elif t in funcname:
                if op == 'bl' or funcname[t]:
                    note = funcname[t] or ('sub_%x' % t)
                elif op == 'b':
                    note = 'sub_%x' % t
            if op == 'bl':
                # a call may clobber x0-x18: forget pages in caller-saved registers except x19+
                for r in [r for r in pages if int(r[1:]) < 19]:
                    pages.pop(r, None)
    elif op in ('mov', 'movz', 'movk', 'movq', 'adr') or op.startswith('fmov') or op.startswith('ldp') or op.startswith('ldur'):
        for r in list(pages):
            if ops.startswith(r + ',') or re.match(r'^[wxdq]\d+, ' + r + r'\b', ops) and op.startswith('ld'):
                pages.pop(r, None)
    if note:
        out.append('%8x:  %-48s ; %s' % (addr, ins, note))
    elif ins:
        out.append('%8x:  %s' % (addr, ins))

open(prefix + '.dis', 'w').write('\n'.join(out) + '\n')
with open(prefix + '.methods', 'w') as f:
    for imp in sorted(methods):
        f.write('%#x  %s\n' % (imp, methods[imp]))
    f.write('# classes: %d, methods: %d, functions: %d, objc stubs: %d\n' % (len(classes), len(methods), len(starts), len(stub_sel)))
print('functions', nfunc, 'methods', len(methods), 'classes', len(classes), 'stubs', len(stub_sel), 'lines', len(out), file=sys.stderr)
