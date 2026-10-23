#!/usr/bin/env python3
"""Audit the actual linked native-ARM diagnostic; never run or deploy it."""
from pathlib import Path
import hashlib, hmac, json, re, struct, subprocess

TREE = Path(__file__).resolve().parents[1]
ROOT = TREE.parent.parent
RUN = ROOT / 'analysis/native-arm-kernel-build-20261024'
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

data = KERNEL.read_bytes()
info = macho(data)
require(sha(BASE) == BASE_SHA, 'Original baseline changed')
baseline = BASE.read_bytes()
old = macho(baseline)
old_pattern = read_va(baseline, old, 0xfffffff0073ea310, 32)
require(struct.unpack_from('<I', old_pattern, 28)[0] == 0xb9000001, 'Baseline final instruction is not STR W1,[X0]')
require(baseline.count(old_pattern) == 1, 'Baseline route pattern is not unique')
require(data.count(old_pattern) == 0, 'Old BCM MPIDR/routing/MMIO store pattern remains in native kernel')
start_obj = TARGET / 'osfmk/DEVELOPMENT/start.o'
require(start_obj.read_bytes().count(old_pattern) == 0, 'Old BCM pattern remains in start.o')
start_record_path = start_obj.with_suffix('.o.json')
start_record = read_record(start_record_path)
require('XNU_NATIVE_ARCH_TIMER=1' in start_record['arguments'] or '-DXNU_NATIVE_ARCH_TIMER=1' in start_record['arguments'],
        'Actual start.o compiler record does not enable native architectural timer')
prng_record_path = TARGET / 'osfmk/DEVELOPMENT/prng_random.o.json'
prng_record = read_record(prng_record_path)
require('XNU_SOURCE_CORECRYPTO14=1' in prng_record['arguments'] or '-DXNU_SOURCE_CORECRYPTO14=1' in prng_record['arguments'],
        'Actual PRNG source-provider bootstrap is disabled')
require('-UXNU_SOURCE_CORECRYPTO14' not in prng_record['arguments'], 'Source provider bootstrap was overridden')
symbol_text = capture(['xcrun', 'nm', '-n', KERNEL])
symbols = {}
for line in symbol_text.splitlines():
    match = re.match(r'^([0-9a-fA-F]+)\s+(\S)\s+(\S+)$', line.strip())
    if match:
        symbols[match[3]] = {'address': int(match[1], 16), 'type': match[2]}
required = ['_armcpu_registered', '_armcpu_gic_ready', '_armcpu_timer_interrupts',
            '_armdt_platform_configured', '_armdt_platform_nvram_proxy',
            '_source_corecrypto14_bootstrap', '_corecrypto_kext_start', '__start',
            '_start_first_cpu', '_sysreg_restore']
code_names = {'_source_corecrypto14_bootstrap', '_corecrypto_kext_start', '__start', '_start_first_cpu'}
public_names = set(required) - {'_source_corecrypto14_bootstrap'}
for name in required:
    require(name in symbols and symbols[name]['type'].upper() != 'U', 'Missing real definition: ' + name)
    entries = info['symbols'].get(name, [])
    require(len(entries) == 1, 'Required symbol not uniquely defined: ' + name)
    definition = entries[0]
    require(definition['n_type'] & 0x0e == 0x0e, 'Required symbol is not N_SECT: ' + name)
    require(not definition['n_desc'] & 0x00c0, 'Weak provider/entry definition is not accepted: ' + name)
    if name in public_names:
        require(definition['n_type'] & 1, 'Expected external provider/entry definition: ' + name)
    index = definition['n_sect']
    require(1 <= index <= len(info['sections']), 'Definition section is out of bounds')
    section = info['sections'][index-1]
    require(section['va'] <= definition['address'] < section['va'] + section['size'], 'Symbol outside its actual section')
    require(bool(section['initial_protection'] & 4) == (name in code_names), 'Wrong code/data section: ' + name)
    if name in code_names:
        require(section['name'] == '__text', 'Code entry is not in actual __text')
    symbols[name].update(definition, section=section['segment'] + ',' + section['name'],
                         strong_definition=True, external=bool(definition['n_type'] & 1))
undefined = capture(['xcrun', 'nm', '-u', KERNEL]).strip()
require(not undefined, 'Final source-only kernel still has undefined symbols: ' + undefined[:500])
tcr = struct.unpack('<Q', read_va(data, info, symbols['_sysreg_restore']['address'], 8))[0]
require(((tcr >> 14) & 3, (tcr >> 30) & 3) == (0, 2), 'Kernel TCR is not 4 KiB for both TTBRs')
require(info['entry'] == symbols['__start']['address'], 'LC_UNIXTHREAD does not point at the actual source entry')
entry_instruction = struct.unpack('<I', read_va(data, info, info['entry'], 4))[0]
require(entry_instruction & 0xfc000000 == 0x14000000, '__start is not a direct AArch64 B')
branch_imm = entry_instruction & 0x03ffffff
if branch_imm & 0x02000000:
    branch_imm -= 0x04000000
branch_target = (info['entry'] + branch_imm * 4) & 0xffffffffffffffff
require(branch_target == symbols['_start_first_cpu']['address'], 'Entry branch misses the primary CPU path')
require('common_start' in symbols, 'Primary common_start label absent')
common = symbols['common_start']['address']
old_common = 0xfffffff0073ea290
# The original #if contains the 32-byte routing sequence and its final 4-byte
# ISB. The unchanged architectural TLBI/IC instructions start at old +0xa4.
join = read_va(baseline, old, old_common + 0x7c, 4) + read_va(baseline, old, old_common + 0xa4, 8)
require(read_va(data, info, common + 0x7c, 12) == join,
        'Native common_start does not directly join pre/post-BCM architectural maintenance instructions')

disassembly = []
for label, path, address, length in [
    ('baseline BCM route reference', BASE, 0xfffffff0073ea310, 32),
    ('native reset entry', KERNEL, info['entry'], 16),
    ('native primary CPU and common_start', KERNEL, symbols['_start_first_cpu']['address'], 0x340)]:
    argv = ['xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn',
            f'--start-address={address:#x}', f'--stop-address={address+length:#x}', str(path)]
    disassembly.extend([label, 'COMMAND: ' + json.dumps(argv), capture(argv)])
(RUN / 'entry-comparison-disassembly.txt').write_text('\n\n'.join(disassembly))

archives = {}
for name, rel, report_name in [
    ('ArmDTPlatform', 'armdt-platform/libArmDTPlatform.a', 'armdt-platform/build-report.json'),
    ('ArmCpu', 'armcpu/libArmCpu.a', 'armcpu/build-report.json'),
    ('corecrypto', 'corecrypto-2023/libcorecrypto_kernel_noasm-namespaced.a', 'corecrypto-2023/build-report-namespaced.json')]:
    p = TREE / 'build' / rel
    report = json.loads((TREE / 'build' / report_name).read_text())
    require(report['archive_sha256'] == sha(p), name + ': archive hash does not match its real build')
    if name == 'corecrypto':
        require(report['failures'] == 0 and report['namespace_isolated'] and not report['assembly_enabled'],
                'Required corecrypto noasm namespaced build failed')
    else:
        require(report['compiler_returncode'] == 0 and report['source_unchanged'], name + ': non-genuine build report')
    archives[name] = {'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p),
                      'members': capture(['xcrun', 'ar', 't', p]).splitlines()}

hmac_path = KERNEL.with_suffix(KERNEL.suffix + '.crypto-hmac.json')
seal = json.loads(hmac_path.read_text())
code_sections = [s for s in info['sections'] if s['name'] == '__text' and s['segment'] in ('__TEXT', '__TEXT_EXEC')]
mac_sections = [s for s in info['sections'] if s['name'] == '__fips_hmacs']
require(code_sections and len(mac_sections) == 1 and mac_sections[0]['size'] == 32,
        'Independent section parse cannot find the real POST code/HMAC fields')
codeoff, codebytes, macoff = code_sections[0]['fileoff'], code_sections[0]['size'], mac_sections[0]['fileoff']
require((codeoff, codebytes, macoff) == (int(seal['code_file_offset'], 16), seal['code_bytes'], int(seal['hmac_file_offset'], 16)),
        'Sealer metadata does not describe the actual Mach-O sections')
require(codeoff <= len(data) and codebytes <= len(data)-codeoff and macoff + 32 <= len(data), 'POST HMAC record bounds')
header_segment = next(s for s in info['segments'] if s['name'] == '__TEXT')
require(code_sections[0]['va'] - header_segment['va'] == codeoff,
        'Runtime header-relative POST mapping does not match file offsets')
require(not codeoff <= macoff < codeoff + codebytes, 'POST HMAC must not overlap its hashed code')
actual_hmac = hmac.digest(b'\0', data[codeoff:codeoff+codebytes], 'sha256')
require(actual_hmac == data[macoff:macoff+32] and actual_hmac.hex() == seal['hmac_sha256'], 'Intact source provider POST HMAC not sealed correctly')
require(seal['kernel_sha256'] == sha(KERNEL), 'Kernel changed after POST HMAC sealing')
require(not seal['runtime_selftests_executed'], 'Host build cannot claim runtime crypto POST')

report = {
    'status': 'PASS', 'purpose': 'host-only source-built native ARM diagnostic',
    'kernel': {'path': str(KERNEL), 'bytes': len(data), 'sha256': sha(KERNEL)},
    'baseline_sha256_unchanged': BASE_SHA,
    'granules': {'initial_tcr': hex(tcr), 'tg0': 0, 'tg1': 2, 'ttbr0_bytes': 4096, 'ttbr1_bytes': 4096},
    'entry_va': hex(info['entry']),
    'entry_branch': {'instruction': hex(entry_instruction), 'target': hex(branch_target), 'matches_start_first_cpu': True},
    'common_start_architectural_join': {'common_start_va': hex(common), 'native_join_va': hex(common+0x7c),
                                       'machine_bytes': join.hex(), 'removed_bcm_conditional_block_bytes': 36,
                                       'bcm_routing_pattern_bytes': 32,
                                       'sequence': 'DSB ISH -> TLBI VMALLE1 -> IC IALLU'},
    'real_provider_and_entry_definitions': {n: {**symbols[n], 'address': hex(symbols[n]['address'])} for n in required},
    'final_undefined_symbols': [],
    'bcm_route_pattern': {'baseline_store_va': '0xfffffff0073ea32c', 'baseline_pattern_start_va': '0xfffffff0073ea310',
                          'pattern_hex': old_pattern.hex(), 'baseline_occurrences': 1,
                          'native_kernel_occurrences': 0, 'native_start_object_occurrences': 0,
                          'note': 'Exact MPIDR/index/address/f0/store instruction sequence; linked addresses may move.'},
    'actual_start_compile_record_sha256': sha(start_record_path),
    'actual_prng_compile_record_sha256': sha(prng_record_path),
    'archives': archives, 'post_hmac': seal,
    'hardware_boot_demonstrated': False, 'memory_ownership_established': False,
    'runtime_crypto_selftests_executed': False, 'prelink_attempted': False,
}
(RUN / 'native-kernel-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: report[k] for k in ('status', 'kernel', 'granules', 'entry_va', 'bcm_route_pattern')}, indent=2))
