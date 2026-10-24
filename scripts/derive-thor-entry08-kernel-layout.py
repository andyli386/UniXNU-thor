#!/usr/bin/env python3
"""Audit the actual linked ENTRY08 normal-XNU-preVM diagnostic; never execute or deploy it."""
from pathlib import Path
import hashlib, hmac, json, re, struct, subprocess, sys

TREE = Path(__file__).resolve().parents[1]
ROOT = TREE.parent.parent
RUN = ROOT / 'analysis/thor-entry08-kernel-build-20261024'
TARGET = TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
KERNEL = TARGET / 'mach.development.bcm2837.unstripped.noctf'
BASE = ROOT / 'sources/UniXNU/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf'
BASE_SHA = 'c262f87c0718a0ccf72bd8dea288ac1dbc7504b3649c66810c2f276d23386908'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def capture(argv):
    return subprocess.check_output([str(x) for x in argv], text=True, timeout=120)

def macho(data):
    require(len(data) >= 32, 'Truncated Mach-O')
    magic, cpu, subtype, kind, count, size = struct.unpack_from('<6I', data)
    require((magic, cpu, subtype, kind) == (0xfeedfacf, 0x100000c, 0, 2), 'Expected ARM64 MH_EXECUTE')
    require(32 + size <= len(data), 'Truncated load commands')
    offset, segments, sections = 32, [], []
    entry, version, symtab = None, None, None
    for _ in range(count):
        require(offset + 8 <= 32 + size, 'Truncated load command')
        cmd, length = struct.unpack_from('<II', data, offset)
        require(length >= 8 and offset + length <= 32 + size, 'Bad load-command bounds')
        if cmd == 0x19:
            require(length >= 72, 'Truncated segment')
            name, va, vmsize, fileoff, filesize, maximum, initial, nsections, flags = struct.unpack_from('<16s4Q4I', data, offset + 8)
            require(fileoff <= len(data) and filesize <= len(data) - fileoff, 'Segment file bounds')
            require(72 + 80 * nsections <= length, 'Section command bounds')
            segments.append({'name': name.rstrip(b'\0').decode(), 'va': va, 'vmsize': vmsize,
                             'fileoff': fileoff, 'filesize': filesize, 'initial_protection': initial})
            for i in range(nsections):
                p = offset + 72 + i * 80
                secname, segname, addr, nbytes, pos = struct.unpack_from('<16s16sQQI', data, p)
                sections.append({'name': secname.rstrip(b'\0').decode(), 'segment': segname.rstrip(b'\0').decode(),
                                 'va': addr, 'size': nbytes, 'fileoff': pos, 'initial_protection': initial})
        elif cmd == 2:
            require(length == 24 and symtab is None, 'Unexpected symbol table command')
            symtab = struct.unpack_from('<4I', data, offset + 8)
        elif cmd == 0x32:
            version = struct.unpack_from('<II', data, offset + 8)
        elif cmd == 5:
            require(length == 288, 'Unexpected ARM64 thread command')
            entry = struct.unpack_from('<Q', data, offset + 272)[0]
        offset += length
    require(offset == 32 + size and entry is not None, 'Incomplete load-command parse')
    require(version == (2, 0x110000), 'Wrong kernel deployment')
    require(symtab is not None, 'Actual symbol table missing')
    symoff, nsyms, stroff, strsize = symtab
    require(symoff <= len(data) and nsyms <= (len(data) - symoff) // 16, 'Symbol table out of bounds')
    require(stroff <= len(data) and strsize <= len(data) - stroff, 'String table out of bounds')
    definitions = {}
    for i in range(nsyms):
        string_index, kind, section, desc, value = struct.unpack_from('<IBBHQ', data, symoff + 16*i)
        if kind & 0xe0:  # Debug/STAB records do not define the provider's linkage.
            continue
        require(string_index < strsize, 'Symbol name offset out of bounds')
        end = data.find(b'\0', stroff + string_index, stroff + strsize)
        require(end >= 0, 'Unterminated symbol name')
        name = data[stroff + string_index:end].decode('utf-8', errors='strict')
        definitions.setdefault(name, []).append({'address': value, 'n_type': kind,
                                                  'n_sect': section, 'n_desc': desc})
    return {'segments': segments, 'sections': sections, 'entry': entry, 'symbols': definitions}

def read_va(data, info, va, nbytes):
    for s in info['segments']:
        if s['va'] <= va and va - s['va'] <= s['filesize'] and nbytes <= s['filesize'] - (va - s['va']):
            pos = s['fileoff'] + va - s['va']
            return data[pos:pos+nbytes]
    raise RuntimeError(f'VA {va:#x}+{nbytes:#x} is not file-backed')

def read_record(path):
    return json.loads(path.read_text().strip().rstrip(','))


import argparse
parser=argparse.ArgumentParser()
parser.add_argument('kernel',type=Path)
parser.add_argument('output',type=Path)
a=parser.parse_args()
data=a.kernel.read_bytes()
info=macho(data)
segments=[s for s in info['segments'] if s['vmsize']]
base=min(s['va'] for s in segments)
end=max(s['va']+s['vmsize'] for s in segments)
require(base==0xfffffff007004000,'Unexpected kernel VA base')
require(end-base<0x4000000-0x200000-0x48000,'Kernel no longer fits diagnostic grant')
loaded=bytearray(end-base)
ranges=[]
for s in segments:
    require(s['filesize']<=s['vmsize'],'Segment file exceeds VM size')
    lo,hi=s['va']-base,s['va']-base+s['vmsize']
    require(lo>=0 and hi<=len(loaded),'Segment outside span')
    require(all(hi<=x or lo>=y for x,y in ranges),'Overlapping load segments')
    ranges.append((lo,hi))
    loaded[lo:lo+s['filesize']]=data[s['fileoff']:s['fileoff']+s['filesize']]
def symbol(name):
    vals={v['address'] for n,vs in info['symbols'].items() if n.lstrip('_')==name.lstrip('_') for v in vs if v['n_sect'] and (v['n_type']&0xe)==0xe}
    require(len(vals)==1,'Missing or ambiguous symbol '+name)
    return vals.pop()
entry=info['entry']
require(symbol('__start')==entry,'Thread entry differs from real __start')
vectors=symbol('LowExceptionVectorBase')
asm_end=symbol('thor_entry08_asm_end')
sync_start=(min(entry,vectors)//4096)*4096
sync_end=((asm_end+4095)//4096)*4096
require(sync_start<=entry<vectors<asm_end<=sync_end,'Entry/vector/helper ordering changed')
require(sync_end-sync_start<=0x4000,'Entry sync interval too large')
obj={'schema':1,'scope':'Actual Mach-O geometry and hashes; semantic execution review is separate',
 'kernel_path':str(a.kernel),'kernel_bytes':len(data),'kernel_sha256':hashlib.sha256(data).hexdigest(),
 'loaded_span_bytes':len(loaded),'loaded_span_sha256':hashlib.sha256(loaded).hexdigest(),
 'segment_count':len(segments),'virtual_base':base,'virtual_end':end,
 'entry_offset':entry-base,'vectors_offset':vectors-base,
 'entry_sync_offset':sync_start-base,'entry_sync_bytes':sync_end-sync_start,
 'entry_va_hex':hex(entry),'vectors_va_hex':hex(vectors),'asm_end_va_hex':hex(asm_end),
 'bootstrap_pagetables_offset':symbol('bootstrap_pagetables')-base,
 'intstack_offset':symbol('intstack')-base,'intstack_top_offset':symbol('intstack_top')-base,
 'excepstack_offset':symbol('excepstack')-base,'excepstack_top_offset':symbol('excepstack_top')-base,
 'hardware_executed':False}
require(not a.output.exists(),'Refusing to overwrite existing layout')
a.output.write_text(json.dumps(obj,indent=2)+'\n')
print(json.dumps({k:v for k,v in obj.items() if k not in ['virtual_base','virtual_end']}))
