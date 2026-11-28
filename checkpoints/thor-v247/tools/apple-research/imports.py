"""imports.py <binary> [substring...]: imported symbol names from LC_DYLD_CHAINED_FIXUPS"""
import struct, sys
p = sys.argv[1]; pats = sys.argv[2:]
data = open(p, 'rb').read()
ncmds, = struct.unpack_from('<I', data, 16)
off = 32; out = []
for _ in range(ncmds):
    cmd, size = struct.unpack_from('<II', data, off)
    if cmd == 0x80000034:
        doff, dsize = struct.unpack_from('<II', data, off + 8)
        ver, starts_off, imports_off, symbols_off, imports_count, imports_format, symbols_format = struct.unpack_from('<7I', data, doff)
        for i in range(imports_count):
            v, = struct.unpack_from('<I', data, doff + imports_off + i * 4)
            z = data.find(b'\0', doff + symbols_off + (v >> 9))
            out.append(data[doff + symbols_off + (v >> 9):z].decode('latin1'))
    elif cmd == 0xb:  # LC_DYSYMTAB classic indirect
        pass
    off += size
out = sorted(set(out))
for n in out:
    if not pats or any(x.lower() in n.lower() for x in pats):
        print(n)
print('# total imports', len(out), file=sys.stderr)
