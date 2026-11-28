"""Minimal Mach-O helper for on-disk Apple binaries: segments, sections, function starts, stubs, cstrings.
   import: m = Macho(path); m.base; m.starts (sorted); m.func(addr) -> (start, end); m.cstr(addr); m.off(addr)"""
import bisect, struct

class Macho:
    def __init__(self, path):
        self.data = data = open(path, 'rb').read()
        self.ncmds, = struct.unpack_from('<I', data, 16)
        self.segs, self.sects = [], []
        self.symtab = self.fstarts = self.entry = None
        off = 32
        for _ in range(self.ncmds):
            cmd, size = struct.unpack_from('<II', data, off)
            if cmd == 0x19:
                name = data[off + 8:off + 24].split(b'\0')[0].decode()
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, off + 24)
                self.segs.append((name, vmaddr, vmsize, fileoff, filesize))
                so = off + 72
                for _s in range(struct.unpack_from('<I', data, off + 64)[0]):
                    sname = data[so:so + 16].split(b'\0')[0].decode()
                    addr, ssize, soff = struct.unpack_from('<QQI', data, so + 32)
                    flags, r1, r2 = struct.unpack_from('<III', data, so + 64)
                    self.sects.append((name, sname, addr, ssize, soff, flags, r1, r2))
                    so += 80
            elif cmd == 0x2:
                self.symtab = struct.unpack_from('<IIII', data, off + 8)
            elif cmd == 0x80000028:    # LC_MAIN
                self.entry, = struct.unpack_from('<Q', data, off + 8)
            elif cmd == 0xB:              # LC_DYSYMTAB
                self.dysym = struct.unpack_from('<18I', data, off + 8)
            elif cmd == 0x26:            # LC_FUNCTION_STARTS
                doff, dsize = struct.unpack_from('<II', data, off + 8)
                self.fstarts = self._uleb(doff, dsize)
            off += size
        self.base = min(a for n, a, s, f, fs in self.segs if n != '__PAGEZERO')
        self.indirect = struct.unpack_from('<%dI' % self.dysym[13], data, self.dysym[12]) if hasattr(self, 'dysym') else []   # indirectsymoff, nindirectsyms
        symoff, nsyms, stroff, strsize = self.symtab
        self.names = []
        for i in range(nsyms):
            strx, ty, sect, desc, value = struct.unpack_from('<IBBHQ', data, symoff + 16 * i)
            z = data.find(b'\0', stroff + strx)
            self.names.append(data[stroff + strx:z].decode('latin1'))
        self._stub = {}
        for n, sname, addr, ssize, soff, flags, r1, r2 in self.sects:
            if (flags & 0xFF) in (0x8, 0x0) and r2 and sname in ('__stubs', '__la_symbol_ptr', '__got', '__nl_symbol_ptr', '__auth_ptr', '__constg_swiftt'):
                for i in range(ssize // r2):
                    idx = r1 + i
                    self._stub[addr + i * r2] = self.names[self.indirect[idx]] if idx < len(self.indirect) and self.indirect[idx] < len(self.names) else None
        self.starts = []                      # LC_FUNCTION_STARTS is delta-encoded: each entry follows the previous one
        acc = 0
        for x in self.fstarts or []:
            acc += x
            self.starts.append(self.base + acc)

    def _uleb(self, off, size):
        out, i, v, s = [], off, 0, 0
        end = off + size
        while i < end:
            b = self.data[i]; i += 1
            v |= (b & 0x7f) << s
            if b < 0x80:
                out.append(v); v = s = 0
            else:
                s += 7
        return out

    def off(self, va):
        for n, a, s, f, fs in self.segs:
            if a <= va < a + s and va - a < fs:
                return f + va - a
        return None

    def cstr(self, va):
        o = self.off(va)
        if o is None:
            return None
        z = self.data.find(b'\0', o)
        if z < 0 or z - o > 400:
            return None
        return self.data[o:z].decode('latin1')

    def func(self, va):
        i = bisect.bisect_right(self.starts, va) - 1
        if i < 0 or i + 1 >= len(self.starts):
            return None
        return self.starts[i], self.starts[i + 1]

    def stub(self, va):
        return self._stub.get(va)
