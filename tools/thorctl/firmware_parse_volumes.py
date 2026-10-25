#!/usr/bin/env python3
"""Read-only offline Qualcomm UEFI/ABL extraction; no firmware is executed.

Inputs are already verified stock images and original partition backups. Outputs
are derived evidence only. Python stdlib suffices for the encountered gzip and
LZMA GUID-defined sections. Bounds and checksums are checked before traversal.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import lzma
import re
import struct
import uuid
from pathlib import Path

GZIP_GUID = '1d301fe9-be79-4353-91c2-d23bc959ae0c'
LZMA_GUID = 'ee4e5898-3914-4259-9d6e-dc7bd79403cf'
MAX_STREAM = 64 * 1024 * 1024

def sha(data):
    return hashlib.sha256(data).hexdigest()

def guid(data):
    return str(uuid.UUID(bytes_le=bytes(data)))

def align(n, a):
    return (n + a - 1) & ~(a - 1)

def safe(s):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', s)[:140]

def elf_headers(data):
    records = []
    for match in re.finditer(b'\x7fELF', data):
        pos = match.start()
        try:
            ident = data[pos:pos+16]
            if ident[5] != 1 or ident[4] not in (1, 2):
                continue
            is64 = ident[4] == 2
            fmt = '<HHIQQQIHHHHHH' if is64 else '<HHIIIIIHHHHHH'
            h = struct.unpack_from(fmt, data, pos+16)
            phoff, phentsize, phnum = h[4], h[8], h[9]
            expected = 56 if is64 else 32
            if phentsize != expected or not 0 < phnum <= 100 or pos+phoff+phentsize*phnum > len(data):
                continue
            ph = []
            for n in range(phnum):
                q = pos+phoff+n*phentsize
                if is64:
                    t, flags, off, va, pa, fs, ms, al = struct.unpack_from('<IIQQQQQQ', data, q)
                else:
                    t, off, va, pa, fs, ms, flags, al = struct.unpack_from('<IIIIIIII', data, q)
                ph.append(dict(type=t, file_offset=hex(off), virtual_address=hex(va),
                               physical_address=hex(pa), file_size=fs, memory_size=ms,
                               flags=flags, alignment=al,
                               bytes_in_container=pos+off+fs <= len(data)))
            records.append(dict(container_offset=hex(pos), elf_class=64 if is64 else 32,
                                machine=h[1], entry=hex(h[3]), program_headers=ph))
        except (struct.error, IndexError):
            continue
    return records

class Parser:
    def __init__(self, root):
        self.root = root
        self.out = root/'analysis/firmware-volumes'
        self.out.mkdir(parents=True, exist_ok=True)
        self.extracted = self.out/'extracted'
        self.extracted.mkdir(exist_ok=True)
        self.streams = self.out/'streams'
        self.streams.mkdir(exist_ok=True)
        self.volumes, self.artifacts, self.warnings = [], [], []

    def save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
        return dict(path=str(path), bytes=len(data), sha256=sha(data))

    def sections(self, data, begin, end):
        result, q = [], begin
        while q+4 <= end:
            if data[q:end].strip(b'\xff\x00') == b'':
                break
            size, typ, hs = int.from_bytes(data[q:q+3], 'little'), data[q+3], 4
            if size == 0xffffff:
                if q+8 > end:
                    raise ValueError('truncated extended section')
                size, hs = struct.unpack_from('<I', data, q+4)[0], 8
            if size < hs or q+size > end:
                raise ValueError(f'out-of-bounds section {q:#x} size {size:#x}')
            s = dict(offset=hex(q), size=size, type=hex(typ), header_size=hs)
            if typ == 0x15:
                s['ui'] = data[q+hs:q+size].decode('utf-16-le').rstrip('\0')
            result.append(s)
            q = align(q+size, 4)
        return result

    def volume(self, data, base, stream, chain, depth=0):
        if depth > 8 or base+56 > len(data) or data[base+40:base+44] != b'_FVH':
            raise ValueError('invalid firmware volume header')
        length = struct.unpack_from('<Q', data, base+32)[0]
        hlen = struct.unpack_from('<H', data, base+48)[0]
        if length < hlen or hlen < 56 or hlen % 2 or base+length > len(data):
            raise ValueError('firmware volume bounds')
        checksum_ok = sum(struct.unpack_from('<'+'H'*(hlen//2), data, base)) & 0xffff == 0
        if not checksum_ok:
            raise ValueError('firmware volume checksum failed')
        v = dict(stream=stream, source_chain=chain, offset=hex(base), size=length,
                 header_size=hlen, header_checksum_ok=checksum_ok,
                 filesystem_guid=guid(data[base+16:base+32]), files=[])
        self.volumes.append(v)
        p, end = align(base+hlen, 8), base+length
        while p+24 <= end:
            h = data[p:p+24]
            if h == b'\xff'*24:
                break
            size, hs = int.from_bytes(h[20:23], 'little'), 24
            if size == 0xffffff:
                if p+32 > end:
                    raise ValueError('truncated extended FFS')
                size, hs = struct.unpack_from('<Q', data, p+24)[0], 32
            if size < hs or p+size > end:
                raise ValueError(f'FFS bounds {p:#x} size {size:#x}')
            hdrsum = (sum(data[p:p+hs])-h[17]-h[23]) & 255
            f = dict(offset=hex(p), size=size, header_size=hs, guid=guid(h[:16]),
                     type=hex(h[18]), attributes=hex(h[19]), state=hex(h[23]),
                     header_checksum_ok=hdrsum == 0)
            if hdrsum:
                raise ValueError(f'FFS header checksum {p:#x}')
            if h[19] & 0x40:
                f['data_checksum_ok'] = (sum(data[p+hs:p+size])+h[17]) & 255 == 0
                if not f['data_checksum_ok']:
                    raise ValueError(f'FFS data checksum {p:#x}')
            if h[18] not in (0xf0, 1):
                f['sections'] = self.sections(data, p+hs, p+size)
                f['ui'] = next((s['ui'] for s in f['sections'] if 'ui' in s), None)
                for index, s in enumerate(f['sections']):
                    q, n, sh, st = int(s['offset'], 16), s['size'], s['header_size'], int(s['type'], 16)
                    provenance = chain+[dict(stream=stream, fv_offset=hex(base),
                                             ffs_offset=hex(p), ffs_guid=f['guid'],
                                             ffs_ui=f['ui'], section_offset=hex(q), section_type=hex(st))]
                    if st in (0x10, 0x12, 0x19):
                        name = f['ui'] or f['guid']
                        ext = {0x10:'.efi', 0x12:'.te', 0x19:'.raw'}[st]
                        if st == 0x19 and Path(name).suffix in ('.cfg','.xml','.csv'):
                            ext = ''
                        dest = self.extracted/safe(stream)/safe(f'{name}{ext}')
                        if dest.exists() and dest.read_bytes() != data[q+sh:q+n]:
                            dest = dest.with_name(f'{safe(name)}-{f["guid"]}-{index}{ext}')
                        a = self.save(dest, data[q+sh:q+n])
                        a.update(ui=f['ui'], section_type=hex(st), source_chain=provenance,
                                 payload_offset=hex(q+sh))
                        self.artifacts.append(a)
                    elif st == 2:
                        if n < sh+20:
                            raise ValueError('GUID-defined section too small')
                        g = guid(data[q+sh:q+sh+16])
                        off, attrs = struct.unpack_from('<HH', data, q+sh+16)
                        if off < sh+20 or off > n:
                            raise ValueError('GUID data offset')
                        s.update(compression_guid=g, data_offset=off, attributes=attrs)
                        compressed = data[q+off:q+n]
                        if g == GZIP_GUID:
                            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as gf:
                                expanded = gf.read(MAX_STREAM+1)
                        elif g == LZMA_GUID:
                            dec = lzma.LZMADecompressor()
                            expanded = dec.decompress(compressed, max_length=MAX_STREAM+1)
                            if not dec.eof:
                                raise ValueError('LZMA stream incomplete or too large')
                        else:
                            self.warnings.append(dict(message='unknown GUID-defined section', guid=g, provenance=provenance))
                            continue
                        if len(expanded) > MAX_STREAM:
                            raise ValueError('decompression limit')
                        sid = safe(f'{stream}-{q:x}')
                        s['expanded'] = self.save(self.streams/(sid+'.bin'), expanded)
                        sub = self.sections(expanded, 0, len(expanded))
                        for subsec in sub:
                            sq = int(subsec['offset'], 16)
                            if int(subsec['type'], 16) == 0x19 and subsec['size'] == subsec['header_size']:
                                s.setdefault('expanded_empty_raw_sections', []).append(subsec)
                                continue
                            if int(subsec['type'], 16) != 0x17:
                                self.warnings.append(dict(message='expanded section is not FV image', section=subsec, stream=sid))
                                continue
                            self.volume(expanded, sq+subsec['header_size'], sid,
                                        provenance+[dict(decompression_guid=g, expanded_sha256=sha(expanded))], depth+1)
                    elif st == 0x17:
                        self.volume(data, q+sh, stream, provenance, depth+1)
            v['files'].append(f)
            p = align(p+size, 8)
        return v

    def run(self):
        stock = self.root/'firmware/stock-extracted'
        backup = self.root/'backups/20261024-000102-CST/partitions'
        sources = []
        for label, name, partition in [('uefi','uefi.elf','uefi'),('abl','abl.elf','abl'),
                                        ('xbl','xbl_s.melf','xbl'),('xbl_config','xbl_config.elf','xbl_config')]:
            path = stock/name
            data = path.read_bytes()
            s = dict(name=label, path=str(path), bytes=len(data), sha256=sha(data), elf_headers=elf_headers(data), original_comparison=[])
            for slot in ('a','b'):
                bp = backup/(partition+'_'+slot+'.img')
                if bp.exists():
                    bd = bp.read_bytes()
                    s['original_comparison'].append(dict(path=str(bp), bytes=len(bd), sha256=sha(bd),
                        stock_prefix_equal=bd[:len(data)]==data, tail_all_zero=not any(bd[len(data):])))
            sources.append(s)
            if label in ('uefi','abl'):
                self.volume(data, 0x1000, 'stock_'+label, [dict(path=str(path), sha256=sha(data))])
        result = dict(schema=1, scope='offline static evidence; no runtime address approval',
                      sources=sources, volumes=self.volumes, artifacts=self.artifacts, warnings=self.warnings)
        self.save(self.out/'firmware-volume-manifest.json', (json.dumps(result, indent=2)+'\n').encode())
        self.memory_map()
        print(json.dumps(dict(volumes=len(self.volumes), artifacts=len(self.artifacts), warnings=len(self.warnings),
                              manifest=str(self.out/'firmware-volume-manifest.json'))))

    def memory_map(self):
        cfg = next(a for a in self.artifacts if a['ui']=='uefiplat.cfg')
        text = Path(cfg['path']).read_text()
        section, rows, parameters = None, [], []
        observed_path = self.root/'analysis/thor-hardware-observed.json'
        observed = json.loads(observed_path.read_text())
        reserved = [dict(path=n['path'], address=int(reg['address'],16), size=int(reg['size'],16))
                    for n in observed['reserved_memory'] for reg in n.get('reg_parent_bus',[])]
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if line.startswith('['):
                section = line
            if not line or line.startswith('#'):
                continue
            if section in ('[MemoryMap]', '[RegisterMap]') and line.startswith('0x'):
                cells = next(csv.reader([line], skipinitialspace=True))
                if len(cells) != 8:
                    raise ValueError(f'memory CSV columns at line {lineno}')
                base, size = int(cells[0],16), int(cells[1],16)
                row = dict(section=section[1:-1], base=hex(base), size=hex(size), end_exclusive=hex(base+size),
                           label=cells[2].strip(), build_hob=cells[3].strip(), resource_type=cells[4].strip(),
                           resource_attributes=cells[5].strip(), efi_memory_type=cells[6].strip(),
                           cache_attributes=cells[7].strip(), source_path=cfg['path'], source_line=lineno,
                           source_sha256=cfg['sha256'], source_text=raw,
                           runtime_fdt_overlaps=[dict(path=r['path'], start=hex(max(base,r['address'])),
                               end_exclusive=hex(min(base+size,r['address']+r['size']))) for r in reserved
                               if base<r['address']+r['size'] and r['address']<base+size],
                           allocation_approved=False)
                rows.append(row)
            elif section=='[ConfigParameters]' and '=' in line:
                k,v = line.split('=',1)
                parameters.append(dict(name=k.strip(), value=v.strip(), source_line=lineno))
        candidate_base, candidate_size = 0xa7100000, 0x300000
        evidence = dict(schema=1, purpose='Input evidence for thor MemoryMapLib; not a ready-to-use map',
            source_config=cfg, runtime_fdt_source=observed['source'],
            runtime_fdt_scope=observed['note'], rows=rows, parameters=parameters,
            runtime_memory=observed['memory'], runtime_reserved_memory=observed['reserved_memory'],
            reference_fd_candidate=dict(base=hex(candidate_base), size=hex(candidate_size), approved=False,
                firmware_overlaps=[r['label'] for r in rows if int(r['base'],16)<candidate_base+candidate_size
                    and candidate_base<int(r['end_exclusive'],16)],
                runtime_fdt_overlaps=[r['path'] for r in reserved if r['address']<candidate_base+candidate_size
                    and candidate_base<r['address']+r['size']]),
            unknowns=['No live GetMemoryMap result at custom payload entry',
                      'No observed relocated ABL instruction/stack or custom payload ranges',
                      'No observed custom-boot DTB/initrd physical extents',
                      'Optional uefiplatLA.cfg override not found in extracted firmware volumes',
                      'TWRP runtime FDT differs in phase from UEFI and is not Android runtime FDT'])
        self.save(self.out/'thor-memory-map-evidence.json', (json.dumps(evidence, indent=2)+'\n').encode())
        cols=['section','base','size','end_exclusive','label','build_hob','resource_type','resource_attributes',
              'efi_memory_type','cache_attributes','source_line','source_sha256','allocation_approved']
        dest=io.StringIO(); w=csv.DictWriter(dest, fieldnames=cols, extrasaction='ignore');w.writeheader();w.writerows(rows)
        self.save(self.out/'thor-memory-map-evidence.csv', dest.getvalue().encode())

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path('/home/vincent/Projects/thor-unixnu'))
    Parser(ap.parse_args().root.resolve()).run()
