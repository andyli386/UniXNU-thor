"""literal decoder for wifid constant objects:  lit.py <hexaddr>..."""
import sys, struct
sys.path.insert(0,'/tmp/claude-501/cont')
from macho import Macho
m = Macho('/tmp/claude-501/cont/bin/wifid')
data = m.data
imports=[]
off=32
for _ in range(m.ncmds):
    cmd, size = struct.unpack_from('<II', data, off)
    if cmd == 0x80000034:
        doff, dsize = struct.unpack_from('<II', data, off+8)
        ver, starts_off, imports_off, symbols_off, imports_count, imports_format, symbols_format = struct.unpack_from('<7I', data, doff)
        for i in range(imports_count):
            v, = struct.unpack_from('<I', data, doff + imports_off + i*4)
            z = data.find(b'\0', doff + symbols_off + (v>>9))
            imports.append(data[doff + symbols_off + (v>>9):z].decode())
    off += size
sects = {s[1]: (s[2], s[3]) for s in m.sects}
def in_sect(a, name):
    if name not in sects: return False
    x, sz = sects[name]; return x <= a < x + sz
def ptr(va):
    v, = struct.unpack_from('<Q', data, m.off(va))
    if v>>63:
        o = v & 0xFFFFFF
        return ('bind', imports[o] if o < len(imports) else o)
    t = v & 0xFFFFFFFFF
    return (t + m.base) if t else 0
def u64(va):
    return struct.unpack_from('<Q', data, m.off(va))[0]
def obj(a):
    if a == 0: return 'nil'
    if isinstance(a, tuple): return str(a)
    if in_sect(a, '__cfstring'):
        p = ptr(a+16)
        return 'CFSTR %r' % (m.cstr(p) if isinstance(p,int) else p)
    if in_sect(a, '__objc_intobj'):
        v = u64(a+16); sv = struct.unpack_from('<q', data, m.off(a+16))[0]
        return 'NSNumber %d/0x%x' % (sv, v & 0xFFFFFFFFFFFFFFFF)
    if in_sect(a, '__objc_floatobj'):
        return 'NSNumber %r' % struct.unpack_from('<d', data, m.off(a+16))[0]
    if in_sect(a, '__objc_arrayobj'):
        n = u64(a+8); p = ptr(a+16)
        items = [obj(ptr(p+8*i)) if isinstance(p,int) else '?' for i in range(n)] if isinstance(p,int) else ['%s' % (p,)]
        return 'NSArray(%s)' % ', '.join(items)
    if in_sect(a, '__objc_dictobj'):
        cnt = u64(a+8); kb = ptr(a+24); vb = kb + 8*cnt
        items = []
        for i in range(cnt):
            items.append('%s: %s' % (obj(ptr(kb+8*i)), obj(ptr(vb+8*i))))
        return 'NSDictionary(%s)' % ', '.join(items)
    s = m.cstr(a)
    if s is not None: return 'cstring %r at %#x' % (s[:80], a)
    return 'object %#x' % a
for arg in sys.argv[1:]:
    a = int(arg, 16)
    print('%#x: %s' % (a, obj(a)))
