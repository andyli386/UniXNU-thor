#!/usr/bin/env python3.13
"""Phone resources for the keyboard of an iPad restore: from the system image of an iPhone restore of the same build.

Upstream's fix for the keyboard (migration/ipados18/services112-wifi-audio-phone-input.md, userland/phone_components18.py):
an iPad restore carries KBLayouts_iPad.dat and no KBLayouts_iPhone.dat, and UIKit on a phone persona asks for a phone
layout, so the keyboard never appears.  The resources of the iPhone restore are added to the same bundles of the
guest: every file under the named bundles except Mach-O files, Info.plist, the bundle's executable, code signatures
and symbolic links.  The iPad layout stays; a file both restores have gets the iPhone version, as upstream does.

The donor is read where it lies (the raw APFS image of its OS member, with the device repository's reader; nothing
is mounted) and the files are written into a guest volume mounted on this Mac: a working copy of the system window
of the phone (see thor/os_patch.py for how that copy then reaches the phone).

    python3.13 -I thor/phone_resources.py copy --donor <iPhone OS image> --manifest <BuildManifest.plist of the iPhone restore>
         --target-root <mounted working copy of the system window> --set keyboard [--dry-run] [--report <json>]

    --set keyboard   TextInputUI, TextInput, TextInputCore, KeyboardServices
    --set wifi       WiFiKitUI
    --set phone      TelephonyUI, PhoneNumbers, CoreTelephony, the telephone settings bundles, the carrier bundles
    --set all        all of them.  The Phone application is left out: it is arm64e and does not run with this cache.

Apple's files stay on this Mac; a report holds paths, sizes and digests only.
"""
import argparse
import hashlib
import json
import plistlib
import stat
import sys
from pathlib import Path

assert __debug__, "verification needs assertions"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools/apfs'))
import apfs                                                     # noqa: E402

MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
         b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca', b'\xfe\xed\xfa\xce'}
PRIVATE = 'System/Library/PrivateFrameworks'
CARRIERS = 'System/Library/Carrier Bundles/iPhone'
SETS = dict(
    keyboard=[f'{PRIVATE}/{name}.framework' for name in ('TextInputUI', 'TextInput', 'TextInputCore', 'KeyboardServices')],
    wifi=[f'{PRIVATE}/WiFiKitUI.framework'],
    phone=[f'{PRIVATE}/{name}.framework' for name in ('TelephonyUI', 'PhoneNumbers')]
         + ['System/Library/Frameworks/CoreTelephony.framework']
         + [f'System/Library/PreferenceBundles/{name}' for name in ('CarrierSettings.bundle', 'CellularNetworkTelephonySettings.bundle',
                                                                    'MobilePhoneSettings.bundle', 'PhonebookTelephonySettings.bundle',
                                                                    'SettingsCellular.bundle')]
         + [CARRIERS],
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(volume, oid, relative, executable, out):
    """every file below a donor directory that goes into the guest"""
    for name, child, _kind in sorted(volume.dir_entries(oid)):
        if name == '_CodeSignature':
            continue
        node = volume.inode(child)
        if node is None:
            continue
        rel = relative / name
        if node.is_dir:
            collect(volume, child, rel, executable, out)
        elif node.is_reg:
            if len(rel.parts) == 1 and name in ('Info.plist', executable):
                continue
            out.append((rel, node))                                # symbolic links and anything else are left out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('copy')
    p.add_argument('--donor', required=True, type=Path, help='the system image (the OS member) of the iPhone restore')
    p.add_argument('--manifest', required=True, type=Path, help='BuildManifest.plist of the iPhone restore')
    p.add_argument('--target-root', required=True, type=Path, help='the mounted working copy of the system window')
    p.add_argument('--set', action='append', choices=('keyboard', 'wifi', 'phone', 'all'), required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--report', type=Path)
    args = parser.parse_args()

    release = plistlib.loads(args.manifest.read_bytes())
    if (release['ProductVersion'], release['ProductBuildVersion']) != ('18.7.10', '22H374'):
        raise SystemExit('the donor restore must be the 18.7.10 / 22H374 build the guest runs')
    if not any(product.startswith('iPhone') for product in release['SupportedProductTypes']):
        raise SystemExit('an iPhone restore is the donor')
    guest = plistlib.loads((args.target_root / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
    if (guest.get('ProductVersion'), guest.get('ProductBuildVersion')) != ('18.7.10', '22H374'):
        raise SystemExit(f'the guest is {guest.get("ProductVersion")} {guest.get("ProductBuildVersion")}, not 18.7.10 / 22H374')
    wanted = []
    for name in args.set:
        wanted += ['keyboard', 'wifi', 'phone'] if name == 'all' else [name]
    relatives = list(dict.fromkeys(relative for name in dict.fromkeys(wanted) for relative in SETS[name]))
    container = apfs.Container(str(args.donor))
    try:
        volume = apfs.pick_volume(container, None)
        donor_version = volume.inode(volume.lookup('/System/Library/CoreServices/SystemVersion.plist')[0]).contents()
        donor = plistlib.loads(donor_version)
        if (donor.get('ProductVersion'), donor.get('ProductBuildVersion')) != ('18.7.10', '22H374'):
            raise SystemExit(f'the donor image is {donor.get("ProductVersion")} {donor.get("ProductBuildVersion")}')
        plan, skipped, absent = [], dict(mach_o=0, same=0), []
        for relative in relatives:
            hit = volume.lookup('/' + relative)
            target = args.target_root / relative
            if not hit or not volume.inode(hit[0]).is_dir:
                absent.append(f'donor: {relative}')
                continue
            if not target.is_dir():
                if relative != CARRIERS:
                    absent.append(f'guest: {relative}')
                    continue
                target.mkdir(parents=True)
            if relative.endswith(('.framework', '.bundle')):
                place = volume.lookup(f'/{relative}/Info.plist')
                executable = plistlib.loads(volume.inode(place[0]).contents()).get('CFBundleExecutable', Path(relative).stem) if place else None
            else:
                executable = None
            files = []
            collect(volume, hit[0], Path(), executable, files)
            for rel, node in files:
                data = node.contents()
                if data[:4] in MACHO:
                    skipped['mach_o'] += 1
                    continue
                dest = target / rel
                if dest.is_symlink():
                    raise SystemExit(f'a symbolic link where a file is due: {dest}')
                if dest.is_file() and dest.stat().st_size == len(data) and sha(dest.read_bytes()) == sha(data):
                    skipped['same'] += 1
                    continue
                plan.append(dict(path=(Path(relative) / rel).as_posix(), bytes=len(data), mode=stat.S_IMODE(node.mode), data=data, exists=dest.exists()))
    finally:
        container.close()
    new = sum(1 for item in plan if not item['exists'])
    total = sum(item['bytes'] for item in plan)
    print(f'{len(plan)} files to write ({total} bytes): {new} new, {len(plan) - new} replacing an iPad file; '
          f'skipped {skipped}; absent: {absent or "none"}', file=sys.stderr)
    if args.dry_run:
        for item in plan:
            print(f'  {"replace" if item["exists"] else "new    "} {item["bytes"]:>10}  {item["path"]}')
        return
    for item in plan:
        dest = args.target_root / item['path']
        assert dest.parent.is_dir() and not dest.is_symlink(), dest
        if dest.exists() or dest.is_symlink():
            dest.chmod(0o644)
            dest.unlink()
        dest.write_bytes(item['data'])
        dest.chmod(item['mode'] or 0o644)
        assert dest.stat().st_size == item['bytes'] and sha(dest.read_bytes()) == sha(item['data']), dest
    report = dict(donor=args.donor.name, build='22H374', products=release['SupportedProductTypes'], sets=sorted(set(wanted)),
                  files=[dict(path=item['path'], bytes=item['bytes'], mode=oct(item['mode']), sha256=sha(item['data']), replaced=item['exists']) for item in plan],
                  skipped=skipped, absent=absent, total_bytes=total)
    if args.report:
        args.report.write_text(json.dumps(report, indent=1) + '\n')
    print(f'written {len(plan)} files ({total} bytes); report {args.report}')


if __name__ == '__main__':
    main()
