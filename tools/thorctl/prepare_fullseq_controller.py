#!/usr/bin/env python3
"""Prepare a FULLSEQ one-use controller from the conventional run layout.

This wrapper only assembles paths and delegates validation/sealing to the
canonical fullseq_controller.py template. It never contacts a phone.
"""
from pathlib import Path
import argparse
import sys

from fullseq_controller import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source', type=Path, required=True)
    if sys.platform == 'darwin':
        default_adb = Path('/Users/<user>/Library/Android/sdk/platform-tools/adb')
        default_fastboot = Path('/Users/<user>/Library/Android/sdk/platform-tools/fastboot')
    elif sys.platform == 'linux':
        default_adb = Path('/usr/bin/adb')
        default_fastboot = Path('/usr/bin/fastboot')
    else:
        default_adb = Path('adb')
        default_fastboot = Path('fastboot')
    parser.add_argument('--adb', type=Path, default=default_adb)
    parser.add_argument('--fastboot', type=Path, default=default_fastboot)
    args = parser.parse_args()
    root = args.root.resolve()
    run = root / 'runs' / args.run_id
    l2 = run / 'l2'
    controller_args = argparse.Namespace(
        root=root,
        run_id=args.run_id,
        offline_proof=l2 / 'package/independent-offline.json',
        manifest=l2 / 'package/manifest.json',
        firmware_release=l2 / 'firmware-release.json',
        source=args.source,
        fd=l2 / 'package/SILICIUM_UEFI.fd',
        image=l2 / 'package/thor-boot.img',
        device_proof=l2 / 'trusted-device-baseline.json',
        pins=l2 / 'controller-pins.json',
        adb=args.adb,
        fastboot=args.fastboot,
    )
    print(prepare(controller_args))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(json_error := {'result': 'STOPPED', 'automatic_retries': 0,
                             'message': str(error)}, file=sys.stderr)
        raise SystemExit(2)
