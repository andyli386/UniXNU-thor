#!/usr/bin/env python3
"""Offline structural and build-composition audit of the generated thor FD.

Does not execute firmware, build code, contact a phone, or modify source files.
Uses the separately implemented FV/FFS parser and binds parsed executable
payloads to the current build outputs and their actual linker input lists.
"""
import argparse
import collections
import datetime
import hashlib
import json
import re
import struct
import uuid
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from firmware_parse_volumes import Parser, LZMA_GUID, align, sha

APRIORI = 'fc510ee7-ffdc-11d4-bd41-0080c73c8881'
FV_IMAGE = '5164644d-ce54-4ae5-afc5-c92581177fb8'
VARIABLE = 'cbd2e4d5-7068-4ff5-b462-9822b4ad8d60'
CAPSULE = '42857f0a-13f2-4b21-8a23-53d3f714b840'
PACKAGE_DIRS = {
    'MdePkg': 'Mu_Basecore/MdePkg',
    'CryptoPkg': 'Mu_Basecore/CryptoPkg',
    'OpensslPkg': 'Silicon/Silicium/OpensslPkg',
    'MdeModulePkg': 'Mu_Basecore/MdeModulePkg',
    'ArmPkg': 'Mu_Basecore/ArmPkg',
    'ArmPlatformPkg': 'Mu_Basecore/ArmPlatformPkg',
    'EmbeddedPkg': 'Mu_Basecore/EmbeddedPkg',
    'UefiCpuPkg': 'Mu_Basecore/UefiCpuPkg',
    'SiliciumPkg': 'Silicon/Silicium/SiliciumPkg',
    'SiliciumNullPkg': 'Silicon/Silicium/SiliciumNullPkg',
    'thorPkg': 'Platforms/Xiaomi/thorPkg',
}
FORBIDDEN = re.compile(
    r'(?i)(GptLib|PartitionDxe|DiskIoDxe|BlockIoDxe|FatDxe|EnhancedFat|'
    r'Ufs|UFS|SdMmc|Nvme|Ahci|Scsi|UsbMassStorage|UsbDevice|UsbBus|'
    r'QcomBds|LinuxLoader|Proprietary|QcomPkg/Binary|/Binaries/)')


def defines(text):
    return dict(re.findall(r'^\s*([A-Z][A-Z0-9_]*)\s*=\s*([^\r\n#]+)', text, re.M))


def inf_sections(text):
    sections, current = collections.defaultdict(list), []
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            current = [s.strip().lower() for s in line[1:-1].split(',')]
        else:
            for name in current:
                sections[name].append(line)
    return dict(sections)


def executable_header(data):
    if data[:2] == b'VZ':
        if len(data) < 40:
            raise ValueError('truncated TE')
        machine, nsections, subsystem, stripped, entry, code, base = struct.unpack_from('<HBBHIIQ', data, 2)
        return dict(format='TE', machine=hex(machine), sections=nsections,
                    subsystem=subsystem, stripped_size=hex(stripped),
                    entry_rva=hex(entry), code_rva=hex(code), image_base=hex(base))
    if data[:2] != b'MZ' or len(data) < 64:
        raise ValueError('unknown executable format')
    off = struct.unpack_from('<I', data, 0x3c)[0]
    if data[off:off+4] != b'PE\0\0':
        raise ValueError('PE signature')
    machine, nsections = struct.unpack_from('<HH', data, off+4)
    opt_size = struct.unpack_from('<H', data, off+20)[0]
    p = off+24
    if opt_size < 112 or p+opt_size > len(data) or struct.unpack_from('<H', data, p)[0] != 0x20b:
        raise ValueError('PE32+ optional header')
    return dict(format='PE32+', machine=hex(machine), sections=nsections,
                entry_rva=hex(struct.unpack_from('<I', data, p+16)[0]),
                image_base=hex(struct.unpack_from('<Q', data, p+24)[0]),
                size_of_image=struct.unpack_from('<I', data, p+56)[0],
                subsystem=struct.unpack_from('<H', data, p+68)[0])


def rebase_sec_te(original_te, original_pe, target_base):
    """Apply only PE DIR64 relocation entries to the stripped TE bytes.

    GenFv rebases SEC after GenFw creates the intermediate TE; therefore a
    pre-GenFv TE hash is not expected to equal the FD payload hash.
    """
    pe = struct.unpack_from('<I', original_pe, 0x3c)[0]
    opt = pe+24
    ns = struct.unpack_from('<H', original_pe, pe+6)[0]
    olen = struct.unpack_from('<H', original_pe, pe+20)[0]
    sections = []
    for i in range(ns):
        q=opt+olen+i*40
        vs,va,rs,rp=struct.unpack_from('<IIII',original_pe,q+8)
        sections.append((va,rs,rp))
    def pe_offset(rva, size):
        for va,rs,rp in sections:
            if va<=rva and rva+size<=va+rs:
                return rp+rva-va
        raise ValueError('PE relocation range outside section')
    reloc_rva, reloc_size=struct.unpack_from('<II',original_pe,opt+112+5*8)
    p=pe_offset(reloc_rva,reloc_size)
    end=p+reloc_size
    oldbase=struct.unpack_from('<Q',original_te,16)[0]
    delta=target_base-oldbase
    fixed=bytearray(original_te)
    struct.pack_into('<Q',fixed,16,target_base)
    te_delta=struct.unpack_from('<H',original_te,6)[0]-40
    te_sections=[]
    for i in range(original_te[4]):
        q=40+i*40
        vs,va,rs,rp=struct.unpack_from('<IIII',original_te,q+8)
        te_sections.append((va,rs,rp-te_delta))
    applied=[]
    while p<end:
        page,size=struct.unpack_from('<II',original_pe,p)
        if size<8 or size%2 or p+size>end:
            raise ValueError('invalid relocation block')
        for n in range(p+8,p+size,2):
            word=struct.unpack_from('<H',original_pe,n)[0]
            typ, rva=word>>12,page+(word&4095)
            if typ==0:
                continue
            if typ!=10:
                raise ValueError('unexpected SEC relocation type')
            matches=[rp+rva-va for va,rs,rp in te_sections if va<=rva and rva+8<=va+rs]
            if len(matches)!=1 or not 0<=matches[0]<=len(fixed)-8:
                raise ValueError('TE relocation target out of bounds')
            off=matches[0]
            old=struct.unpack_from('<Q',fixed,off)[0]
            new=(old+delta)&((1<<64)-1)
            struct.pack_into('<Q',fixed,off,new)
            applied.append(dict(type='DIR64',rva=hex(rva),te_file_offset=hex(off),before=hex(old),after=hex(new)))
        p+=size
    return bytes(fixed), dict(old_image_base=hex(oldbase),new_image_base=hex(target_base),delta=hex(delta),relocations=applied)


class Audit:
    def __init__(self, root, source=None, out=None):
        self.root = root
        self.source = Path(source).resolve() if source else root/'sources/uefi-thor-entry10g'
        self.build = self.source/'Build/thorEntry09/RELEASE_CLANGPDB'
        self.out = Path(out).resolve() if out else root/'analysis/thorctl/audit'
        if not self.source.is_dir() or not self.build.is_dir():
            raise RuntimeError('UEFI source or build output not found')
        self.out.mkdir(parents=True, exist_ok=True)
        self.inputs = {}
        self.failures = []
        self.conditions = []

    def record(self, path):
        path = Path(path)
        data = path.read_bytes()
        obj = dict(path=str(path), bytes=len(data), sha256=sha(data))
        self.inputs[str(path)] = obj
        return obj

    def check(self, condition, message):
        self.conditions.append(dict(condition=message, passed=bool(condition)))
        if not condition:
            self.failures.append(message)
        return bool(condition)

    def resolve(self, relative):
        package, sep, rest = relative.partition('/')
        if package not in PACKAGE_DIRS or not sep or '..' in Path(rest).parts:
            raise ValueError('unresolved package-relative input: '+relative)
        return self.source/PACKAGE_DIRS[package]/rest

    def fdf_inputs(self, path, visited=None):
        visited = set() if visited is None else visited
        if str(path) in visited:
            return []
        visited.add(str(path))
        self.record(path)
        result = []
        for line in path.read_text().splitlines():
            line = line.split('#', 1)[0].strip()
            if line.startswith('!include '):
                result += self.fdf_inputs(self.resolve(line.split()[1]), visited)
            elif line.startswith('INF '):
                result.append(line.split()[1])
        return result

    def build_module(self, module, artifact):
        relative = Path(module['inf_relative'])
        bdir = self.build/'AARCH64'/relative.parent/relative.stem
        op = bdir/'OUTPUT'
        pe_path = op/(module['name']+'.efi')
        make_path = bdir/'GNUmakefile'
        inputs_path = op/'static_library_files.lst'
        map_path = op/(module['name']+'.map')
        rec = dict(build_output=self.record(pe_path), makefile=self.record(make_path),
                   link_response=self.record(inputs_path), link_map=self.record(map_path))
        mk = defines(make_path.read_text())
        ffsdir = Path(mk['FFS_OUTPUT_DIR'].strip())
        raw = Path(artifact['path']).read_bytes()
        section_files = list(ffsdir.glob('*.te' if artifact['section_type']=='0x12' else '*.pe32'))
        section_matches = []
        for path in section_files:
            b = path.read_bytes()
            header = 8 if b[:3] == b'\xff'*3 else 4
            if b[header:] == raw:
                section_matches.append(self.record(path))
        rec['exact_generated_section_matches'] = section_matches
        if artifact['section_type']=='0x10':
            self.check(len(section_matches)==1, module['name']+': payload must equal exactly one generated section')
            rec['payload_equals_output_efi'] = pe_path.read_bytes() == raw
            self.check(rec['payload_equals_output_efi'], module['name']+': FD PE differs from build output')
        else:
            # SEC is explicitly rebased/relocation-stripped and converted to TE.
            # Preserve both the original PE link product and the final TE match.
            originals=list(ffsdir.glob('*Te.raw'))
            if len(originals)!=1:
                raise ValueError('SEC pre-GenFv TE input count')
            rec['pre_genfv_te']=self.record(originals[0])
            inf=self.build/'FV/FVMAIN_COMPACT.inf'
            rec['genfv_configuration']=self.record(inf)
            base=int(defines(inf.read_text())['EFI_BASE_ADDRESS'].strip(),0)
            adjusted, relocation=rebase_sec_te(originals[0].read_bytes(),pe_path.read_bytes(),base)
            rec['sec_relocation']=relocation
            rec['payload_equals_independently_rebased_te']=adjusted==raw
            self.check(adjusted==raw, 'SEC: not equal to independent DIR64 rebase of generated TE')
            rec['sec_conversion_rule'] = [l.strip() for l in make_path.read_text().splitlines()
                                          if ('GenFw ' in l and ('-t ' in l or '-s ' in l))]
        rec['libraries'] = [str(Path(l.strip())) for l in inputs_path.read_text().splitlines() if l.strip()]
        rec['forbidden_link_inputs'] = [l for l in rec['libraries'] if FORBIDDEN.search(l)]
        self.check(not rec['forbidden_link_inputs'], module['name']+': forbidden storage/proprietary library input')
        # Map evidence is preserved but text names alone are not treated as proof
        # of a driver: core UEFI interfaces and VariableFlashInfoLib may mention I/O.
        rec['forbidden_link_map_paths'] = sorted(set(m.group(0) for m in re.finditer(r'/[^\s:]+', map_path.read_text())
                                                    if FORBIDDEN.search(m.group(0))))
        self.check(not rec['forbidden_link_map_paths'], module['name']+': forbidden object in link map')
        return rec

    def run(self):
        fd = self.build/'FV/SILICIUM_UEFI.fd'
        data = fd.read_bytes()
        digest = sha(data)
        label = 'thor_fd_'+digest[:12]
        source = dict(path=str(fd), bytes=len(data), sha256=digest)
        self.inputs[str(fd)] = source
        fdf = self.source/'Platforms/Xiaomi/thorPkg/thor.fdf'
        infs = list(dict.fromkeys(self.fdf_inputs(fdf)))
        expected = []
        for relative in infs:
            path = self.resolve(relative)
            text = path.read_text()
            d = {k:v.strip() for k,v in defines(text).items()}
            expected.append(dict(name=d['BASE_NAME'], guid=d['FILE_GUID'].lower(),
                                 module_type=d['MODULE_TYPE'], inf_relative=relative,
                                 inf=self.record(path)))
            self.check(not any(k.startswith('binaries') and v for k,v in inf_sections(text).items()),
                       relative+': binary-only INF entry')
        self.check(len(expected)==20, 'expected reviewed whitelist has exactly 20 executable modules')
        self.check([(e['name'], e['guid']) for e in expected if e['name'].endswith('BdsDxe')] ==
                   [('ThorEntryBdsDxe', '6494e5d3-a6c3-4c55-82d2-1d7bc50b0e06')],
                   'exactly the ENTRY06 BDS is selected')
        names = {e['guid']:e['name'] for e in expected}
        pars = Parser(self.root)
        pars.volume(data, 0, label, [source])
        self.check(not pars.warnings, 'unparsed section/compression warnings')
        self.check(len(pars.volumes)==2, 'exactly two expected firmware volumes')
        self.check(pars.volumes[0]['size']==len(data)==0x300000, 'FD must be exactly one 3 MiB outer FV')
        stream_data = {label:data}
        for v in pars.volumes:
            for f in v['files']:
                for s in f.get('sections', []):
                    if 'expanded' in s:
                        stream_data[Path(s['expanded']['path']).stem] = Path(s['expanded']['path']).read_bytes()
        actual = []
        compression, raw_artifacts, opaque = [], [], []
        for v in pars.volumes:
            stream = stream_data[v['stream']]
            base = int(v['offset'],16)
            cursor = align(base+v['header_size'],8)
            for f in v['files']:
                off, size = int(f['offset'],16), f['size']
                self.check(not stream[cursor:off].strip(b'\xff\x00'), 'nonpadding gap before FFS')
                cursor = align(off+size,8)
                sections = f.get('sections',[])
                if f['type']=='0xf0':
                    padding=bytearray(stream[off+f['header_size']:off+size])
                    # GenFv embeds EFI_FIRMWARE_VOLUME_EXT_HEADER in a pad FFS.
                    ext=struct.unpack_from('<H',stream,base+52)[0]
                    if ext:
                        begin=base+ext
                        extsize=struct.unpack_from('<I',stream,begin+16)[0]
                        self.check(extsize==20 and begin>=off+f['header_size'] and begin+extsize<=off+size,
                                   'unexpected FV extension header layout')
                        if extsize==20:
                            v['extension_header']=dict(offset=hex(begin),size=extsize,
                                fv_name_guid=str(uuid.UUID(bytes_le=stream[begin:begin+16])))
                            rel=begin-off-f['header_size']
                            padding[rel:rel+extsize]=b'\x00'*extsize
                    self.check(not padding.strip(b'\xff\x00'), 'nonpadding FFS pad data outside FV extension header')
                    continue
                executables = [s for s in sections if s['type'] in ('0x10','0x12')]
                if executables:
                    self.check(len(executables)==1, 'exactly one executable section per module')
                    self.check(f['guid'] in names, 'module outside INF whitelist: '+f['guid'])
                    self.check(f.get('ui') in (None,names.get(f['guid'])), 'module UI/INF BASE_NAME mismatch')
                    allowed = {'0x10','0x12','0x13','0x14','0x15','0x18','0x19'}
                    self.check(all(s['type'] in allowed for s in sections), 'unknown section inside executable FFS')
                    for s in sections:
                        if s['type']=='0x18':
                            q=int(s['offset'],16)+s['header_size']; end=int(s['offset'],16)+s['size']
                            sg=str(uuid.UUID(bytes_le=stream[q:q+16]))
                            self.check(sg=='04132c8d-0a22-4fa8-826e-8bbfefdb836c' and not any(stream[q+16:end]),
                                       'unrecognized nonzero freeform section')
                            s['verified_zero_alignment_padding']=True
                            s['subtype_guid']=sg
                    a = next(a for a in pars.artifacts if a['section_type'] in ('0x10','0x12')
                             and a['source_chain'][-1]['ffs_guid']==f['guid'])
                    e = next(e for e in expected if e['guid']==f['guid'])
                    h = executable_header(Path(a['path']).read_bytes())
                    self.check(h['machine']=='0xaa64', e['name']+': executable must be AArch64')
                    actual.append(dict(**e, artifact=a, executable=h, build=self.build_module(e,a)))
                elif f['guid']==APRIORI:
                    self.check(len(sections)==1 and sections[0]['type']=='0x19', 'APRIORI must contain one RAW list')
                elif f['guid']==FV_IMAGE:
                    self.check(len(sections)==1 and sections[0].get('compression_guid')==LZMA_GUID,
                               'FV image must contain expected LZMA GUID-defined section')
                    compression += sections
                else:
                    opaque.append(f)
                for s in sections:
                    if s['type']=='0x19' and f['guid']!=APRIORI:
                        raw_artifacts.append(dict(ffs_guid=f['guid'], name=names.get(f['guid']),section=s))
            self.check(not stream[cursor:base+v['size']].strip(b'\xff\x00'), 'nonpadding unparsed FV tail')
        self.check(not opaque, 'opaque/unexpected nonexecutable FFS')
        self.check(len(compression)==1, 'exactly one LZMA encapsulation')
        self.check(collections.Counter(a['guid'] for a in actual)==collections.Counter(list(names)),
                   'actual module GUID multiset differs from reviewed INF whitelist')
        # PcdDxe includes its generated RAW database; validate whole data payload.
        for rec in raw_artifacts:
            self.check(rec['name']=='PcdDxe', 'unexpected RAW data in executable FFS')
            a = next(a for a in pars.artifacts if a['section_type']=='0x19'
                     and a['source_chain'][-1]['ffs_guid']==rec['ffs_guid'])
            raw = Path(a['path']).read_bytes()
            matches = []
            for p in (self.build/'FV/Ffs').glob(rec['ffs_guid'].upper()+'*/*'):
                if p.is_file() and p.suffix.lower() not in ('.map','.txt'):
                    b=p.read_bytes()
                    if len(b)>=4 and b[3]==0x19 and b[4:]==raw:
                        matches.append(self.record(p))
            # Some builds put the PCD database directly in a build-side RAW file.
            if not matches:
                for p in (self.build/'FV/Ffs').iterdir():
                    if p.is_dir() and p.name[:36].lower()==rec['ffs_guid']:
                        for q in p.iterdir():
                            if q.is_file():
                                b=q.read_bytes()
                                if len(b)>=4 and b[3]==0x19 and b[4:]==raw:
                                    matches.append(self.record(q))
            rec['generated_section_matches']=matches
            self.check(len(matches)==1, 'PCD RAW database does not match generated section')
        apr_path = self.source/'Platforms/Xiaomi/thorPkg/Include/APRIORI.inc'
        expected_order = []
        for relative in re.findall(r'^\s*INF\s+(\S+)',apr_path.read_text(),re.M):
            expected_order.append(defines(self.resolve(relative).read_text())['FILE_GUID'].strip().lower())
        apr = next(a for a in pars.artifacts if a['section_type']=='0x19'
                   and a['source_chain'][-1]['ffs_guid']==APRIORI)
        ar = Path(apr['path']).read_bytes()
        self.check(len(ar)%16==0, 'APRIORI byte count')
        order = [str(uuid.UUID(bytes_le=ar[i:i+16])) for i in range(0,len(ar),16)]
        self.check(order==expected_order, 'actual APRIORI ordering differs from source')
        self.check(VARIABLE in order and CAPSULE in order and order.index(VARIABLE)<order.index(CAPSULE),
                   'VariableRuntimeDxe must precede CapsuleRuntimeDxe')
        libraries = []
        paths = sorted(set(p for a in actual for p in a['build']['libraries']))
        for path in paths:
            lib = Path(path)
            mkpath = lib.parent.parent/'GNUmakefile'
            mk = {k:v.strip() for k,v in defines(mkpath.read_text()).items()}
            infpath = Path(mk['MODULE_DIR'])/mk['MODULE_FILE']
            inftext = infpath.read_text()
            ss = inf_sections(inftext)
            binary_entries = {k:v for k,v in ss.items() if k.startswith('binaries') and v}
            source_entries = {k:v for k,v in ss.items() if k.startswith('sources') and v}
            allowed_source = any(infpath.is_relative_to(self.source/d) for d in PACKAGE_DIRS.values())
            rec = dict(artifact=self.record(lib), makefile=self.record(mkpath), inf=self.record(infpath),
                       name=mk['MODULE_NAME'], binary_entries=binary_entries,
                       source_entries=source_entries, allowed_source_package=allowed_source)
            libraries.append(rec)
            self.check(allowed_source and not binary_entries and bool(source_entries),
                       'library is not fully source-selected in allowed packages: '+path)
        # Snapshot configuration that determines variable/capsule behavior; the
        # separate source review must assess semantics, not just these names.
        config = []
        for suffix in ['Platforms/Xiaomi/thorPkg/thor.dsc',
                       'Platforms/Xiaomi/thorPkg/Library/PlatformSecLib/PlatformSec.c',
                       'Platforms/Xiaomi/thorPkg/Library/PlatformPeiLib/PlatformPei.c',
                       'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe.c']:
            p=self.source/suffix
            if p.exists():
                rec=self.record(p)
                rec['notable_lines']=[dict(line=n,text=l) for n,l in enumerate(p.read_text().splitlines(),1)
                    if any(t in l for t in ['PcdEmuVariableNvModeEnable','PcdSupportUpdateCapsuleReset',
                        'PcdSupportProcessCapsuleAtRuntime','DxeCapsuleLibNull','PlatformSecLib|','PlatformPeiLib|'])]
                config.append(rec)
        self.check(fd.read_bytes()==data,'FD changed during audit')
        changed = [p for p,o in self.inputs.items() if sha(Path(p).read_bytes())!=o['sha256']]
        self.check(not changed,'build/source evidence changed during audit: '+','.join(changed))
        result = dict(schema=1, audited_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            fd=source, status='PASS' if not self.failures else 'FAIL', failures=self.failures,
            conditions_count=len(self.conditions), conditions=self.conditions,
            scope='Offline FV/FFS/LZMA structure, executable whitelist, build-output identity and actual linker input provenance; not hardware or transient-boot approval',
            expected_modules=expected, actual_modules=actual, volumes=pars.volumes,
            compression=compression, raw_sections=raw_artifacts, opaque_ffs=opaque,
            apriori=dict(artifact=apr, expected_guids=expected_order, actual_guids=order,
                         actual_names=[names.get(g,g) for g in order], exact_order_match=order==expected_order,
                         variable_before_capsule=order.index(VARIABLE)<order.index(CAPSULE)),
            unique_link_inputs=libraries, configuration_evidence=config, parser_warnings=pars.warnings,
            inputs=list(self.inputs.values()), limitations=[
                'Source paths/link metadata identify the build inputs; this is not a reproducible-build proof against a compromised compiler.',
                'UEFI core, variable emulation and generic library interfaces can include I/O-related names without adding a physical storage driver.',
                'No claim is made that stock ABL, inherited DMA hardware or the entire boot lifecycle cannot write storage.',
                'No approval of framebuffer scanout, memory ownership, GICR or fastboot header handling follows from composition audit.'])
        manifest = self.out/(label+'-audit.json')
        manifest.write_text(json.dumps(result,indent=2)+'\n')
        manifest.chmod(0o600)
        lines = ['# Thor FD independent composition audit','',
                 '- Result: **'+result['status']+'**',
                 '- FD: `'+str(fd)+'`',
                 '- SHA256: `'+digest+'`','- Size: '+str(len(data))+' bytes',
                 '- FV count: '+str(len(pars.volumes))+'; executable modules: '+str(len(actual)),
                 '- All FV / FFS checksums checked; one bounded LZMA encapsulation parsed recursively.',
                 '- Actual executable GUID multiset matches the 20 source INF whitelist entries.',
                 '- All 19 extracted PE images match their build outputs byte for byte; SEC TE matches independently applied PE DIR64 relocations and the GenFv base.',
                 '- The SEC alignment section contains only its standard subtype GUID and zero bytes; the pad FFS contains only the FV extension header.',
                 '- Actual APRIORI raw GUID order matches source; VariableRuntimeDxe precedes CapsuleRuntimeDxe.',
                 '- '+str(len(libraries))+' distinct linker inputs all resolve to source INF entries under reviewed packages; no binary INF entries.',
                 '- No GptLib, storage/USB drivers, proprietary firmware modules or proprietary binary paths in selected link inputs/maps.','',
                 '## Actual executable modules','', '| Module | GUID | Executable SHA256 |','|---|---|---|']
        for a in actual:
            lines.append('| '+a['name']+' | `'+a['guid']+'` | `'+a['artifact']['sha256']+'` |')
        lines += ['', '## Actual APRIORI sequence','', ' → '.join(result['apriori']['actual_names']), '',
                  '## Bounds of this result','']+['- '+x for x in result['limitations']]
        if self.failures:
            lines += ['', '## Failures','']+['- '+x for x in self.failures]
        report=self.out/(label+'-audit.md')
        report.write_text('\n'.join(lines)+'\n'); report.chmod(0o600)
        print(json.dumps(dict(status=result['status'],sha256=digest,bytes=len(data),volumes=len(pars.volumes),
              executables=len(actual),link_inputs=len(libraries),failures=self.failures,manifest=str(manifest),report=str(report))))
        return 0 if not self.failures else 1


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=Path('/home/vincent/Projects/thor-unixnu'))
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    root=args.root.resolve()
    assert args.source.resolve().is_relative_to(root/'sources') or args.source.resolve().is_relative_to(root/'worktrees/uefi'), 'Unexpected source root'
    assert args.out.resolve().is_relative_to(root/'runs'), 'Audit output must be in runs/'
    raise SystemExit(Audit(root,args.source,args.out).run())
