#!/usr/bin/env python3
"""Link actual QCA6490 ath11k PCI/MHI and the Thor Darwin24 network/platform code.

From Pong's script. Differences for Thor: the board profile is Thor's own
(thor-wifi-sm8475.json), and no firmware is embedded: the driver reads the
stock files from the phone's modem partition at run time.
"""
import argparse, json, os, struct, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pong_paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tree', type=Path, required=True)
    p.add_argument('--ath11k', type=Path, default=pong_paths.wifi_build() / 'libPongAth11k.a')
    a = p.parse_args()
    out = a.tree / 'build/pong-wifi'
    out.mkdir(parents=True, exist_ok=True)
    shared = pong_paths.sheng_root()
    profile = json.loads((ROOT / 'configs-thor/thor-wifi-sm8475.json').read_text())
    if (profile['board'], profile['soc'], profile['pci']['device']) != ('thor', 'SM8475', 0x1103):
        raise RuntimeError('Thor QCA6490 profile required')
    header = 'struct PongWifiPhyEntry {unsigned off,value,delay;};\nstatic constexpr PongWifiPhyEntry kPongWifiPhy[]={\n' + ''.join(
        '{%d,%d,%d},\n' % tuple(e) for e in profile['pcie']['phy_sequence']) + '};\n'
    header += 'struct PongWifiRail {const char *name;unsigned mv,maxMv,ppid,type,subtype;};\nstatic constexpr PongWifiRail kPongWifiRails[]={\n'
    for e in profile['rails']:
        physical = e.get('physical', {})
        ppid = (physical.get('spmi_slave', 0) << 8) | physical.get('peripheral', 0)
        header += '{%s,%d,%d,%d,%d,%d},\n' % (json.dumps(e['resource']), (e['request'][0] + 999) // 1000,
                                               e['request'][1] // 1000, ppid, physical.get('type', 0), physical.get('subtype', 0))
    for e in profile['pcie']['analogue_rails']:
        header += '{%s,%d,%d,0,0,0},\n' % (json.dumps(e['resource']), (e['microvolts'] + 999) // 1000, e['microvolts'] // 1000)
    header += '};\n'
    domain = profile['pcie']['power_domain']
    if (domain['control'], domain['collapse_vote'], domain['collapse_bit']) != (0x17b004, 0x162128, 0):
        raise RuntimeError('Thor PCIe0 power domain required')
    header += 'static constexpr unsigned kPongPcieCollapseVote=%du,kPongPcieCollapseMask=%du;\n' % (
        domain['collapse_vote'] - 0x100000, 1 << domain['collapse_bit'])
    pcie = profile['pcie']
    # Thor's live DT: 100 MHz at index 10, 19.2 MHz at index 2 (zizhan's layout).
    if pcie['clock_names'][8] != 'pcie_phy_refgen_clk' or pcie['max_clock_frequency_hz'][10] != 100000000:
        raise RuntimeError('Thor pci-msm refgen clock plan changed; adapt its RCG before building')
    for name, value in (('kPongPciePhyStatus', pcie['phy_status_offset']),
                        ('kPongPciePhyStatusMask', 1 << pcie['phy_status_bit']),
                        ('kPongPciePhyPowerDown', pcie['phy_power_down_offset']),
                        ('kPongPcieAuxMhz', pcie['aux_clock_mhz']),
                        ('kPongPcieEpLatencyMs', pcie['endpoint_latency_ms']),
                        ('kPongPcieTargetSpeed', pcie['target_link_speed']),
                        ('kPongPciePerstDelayUs', pcie['perst_delay_us'])):
        header += 'static constexpr unsigned %s=%du;\n' % (name, value)
    # v237: the unattended association fixture (PongWifiPlatform.cpp joinNetwork) reads this header.
    # Only a local private file may provide credentials (PONG_WIFI_TEST_CONFIG, a JSON object with
    # "ssid" and "psk"); they never go into boot args, logs or source control.
    test_path = os.environ.get('PONG_WIFI_TEST_CONFIG')
    ssid = key = b''
    if test_path and Path(test_path).is_file():
        test = json.loads(Path(test_path).read_text())
        ssid = test['ssid'].encode('utf-8'); key = test['psk'].encode('utf-8')
        if not 1 <= len(ssid) <= 32 or not 8 <= len(key) <= 64 or b'\0' in ssid + key:
            raise RuntimeError('Wi-Fi test requires a valid SSID and WPA2 PSK')
        if len(key) == 64 and any(c not in b'0123456789abcdefABCDEF' for c in key):
            raise RuntimeError('64-byte Wi-Fi test PSK must be hexadecimal')
    test_header = 'static constexpr unsigned kWifiTestSsidBytes=%d,kWifiTestKeyBytes=%d;\n' % (len(ssid), len(key))
    for name, data in [('kWifiTestSsid', ssid), ('kWifiTestKey', key)]:
        test_header += 'static const unsigned char %s[]={%s};\n' % (name, ','.join(str(v) for v in data) or '0')
    test_file = out / 'PongWifiTestInputs.h'
    fd = os.open(test_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(test_header)
    print('Wi-Fi association fixture:', 'private network configured' if ssid else 'no private network configured')
    (out / 'PongWifiInputs.h').write_text(header)
    framework = a.tree / '.cache/fakeroot/System/Library/Frameworks/Kernel.framework/Versions/A'
    env = dict(os.environ, DEVELOPER_DIR=str(pong_paths.developer_dir()))
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], env=env, text=True).strip()
    flags = ['-target', 'arm64-apple-ios18.0', '-isysroot', sdk, '-mkernel', '-ffreestanding', '-fno-builtin',
             '-fno-exceptions', '-fno-rtti', '-fno-stack-protector', '-mgeneral-regs-only', '-std=gnu++17', '-fblocks',
             '-O2', '-DKERNEL=1', '-DKERNEL_PRIVATE=1', '-DXNU_KERNEL_PRIVATE=1', '-DARM64_BOARD_CONFIG_PONG=1',
             '-I' + str(framework / 'PrivateHeaders'), '-I' + str(framework / 'Headers'),
             '-I' + str(shared / 'drivers/Ath12kWifi/include'), '-I' + str(shared / 'package'), '-I' + str(out),
             '-I' + str(ROOT / 'drivers/PongPower'), '-Wno-deprecated-declarations']
    objects = []
    for name in ('PongWifiPlatform', 'PongWifiNet'):
        obj = out / (name + '.o')
        cmd = ['xcrun', 'clang++', *flags, '-c', str(ROOT / 'drivers/PongWifi' / (name + '.cpp')), '-o', str(obj)]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        (out / (name + '.log')).write_text(r.stdout + r.stderr)
        if r.returncode:
            raise SystemExit(r.stderr)
        if name == 'PongWifiNet':
            # Register this owned object's network metaclasses after the radio starts.
            # Preserve all code/relocations; LLVM cannot globalize Mach-O symbols.
            raw = bytearray(obj.read_bytes()); offset = 32; symcmd = None
            for _ in range(struct.unpack_from('<I', raw, 16)[0]):
                kind, length = struct.unpack_from('<II', raw, offset)
                if kind == 0x19:
                    for i in range(struct.unpack_from('<I', raw, offset + 64)[0]):
                        section = offset + 72 + 80 * i
                        if raw[section:section + 16].rstrip(b'\0') == b'__mod_init_func':
                            raw[section:section + 16] = b'__pong_init'.ljust(16, b'\0')
                            section_flags = struct.unpack_from('<I', raw, section + 64)[0]
                            struct.pack_into('<I', raw, section + 64, section_flags & ~255)
                elif kind == 2:
                    symcmd = offset
                offset += length
            if symcmd is None:
                raise RuntimeError('owned network object missing symbols')
            symoff, count, stroff, strsize = struct.unpack_from('<4I', raw, symcmd + 8)
            if stroff + strsize != len(raw):
                raise RuntimeError('unexpected network string table')
            for i in range(count):
                at = symoff + 16 * i; n = stroff + struct.unpack_from('<I', raw, at)[0]; end = raw.index(0, n)
                if raw[n:end] == b'__GLOBAL__sub_I_PongWifiNet.cpp':
                    new = b'_PongWifiNetStaticInit\0'; struct.pack_into('<I', raw, at, strsize); raw[at + 4] |= 1
                    raw.extend(new); struct.pack_into('<I', raw, symcmd + 20, strsize + len(new)); break
            else:
                raise RuntimeError('owned network constructor not found')
            obj = out / 'PongWifiNet.late.o'; obj.write_bytes(raw)
        objects.append(obj)
    obj = out / 'PongQcomRpmh.o'
    subprocess.run(['xcrun', 'clang', *flags[:flags.index('-std=gnu++17')], '-O2', '-I' + str(shared / 'package'),
                    '-c', str(shared / 'package/QcomRpmh.c'), '-o', str(obj)], env=env, check=True)
    objects.append(obj)
    archive = out / 'libPongWifiNative.a'; archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'libtool', '-static', '-o', str(archive), str(a.ath11k), *map(str, objects)], env=env, check=True)
    (out / 'build-report.json').write_text(json.dumps(dict(
        board='Thor SM8475 QCA6490', guest='22H374', backend='Linux v7.0.14 ath11k (zizhan driver tree)',
        archive=str(archive), firmware='runtime: modem partition image/qca6490', firmware_embedded=False,
        image_verification='skipped by request', runtime_observed=False), indent=2) + '\n')
    print(archive)


if __name__ == '__main__':
    main()
