#!/usr/bin/env python3
"""Sparse prelink injection for the thor bare kernel (iPadOS 18.7.10 / 22H374).

Extracts the Apple system kext closure the bare kernel's kext references need
(fixed prelink VA 0xfffffff00b000000) from pong's decompressed original
kernelcache and emits the bootable prelinked kernel.

Without this step the bare kernel panics in OSMetaClass::postModLoad (dangling
pointers into the prelink region). Inputs:
  - native:   thor bare kernel from build-ipados18-pong-macos.py
              (mach.development.pong)
  - original: UniXNU-pong/apple/ipados18-22H374/components/
              kernelcache.release.ipad7c.decompressed
  - bindings: UniXNU-pong/mac-build/native-ufs/bindings.json
              (fixed_prelink_base anchors the slide)

Run: python3 -I scripts/prelink-thor-kernel.py [--native PATH] [--output PATH]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))

from emit_prelink import emit, COALESCED  # noqa: E402
from native_prelink import arm64_image, macho, plan  # noqa: E402

DEFAULT_NATIVE = ROOT.parent / 'thor-out/kernel/mach.development.pong'
DEFAULT_ORIGINAL = (ROOT / 'apple/ipados18-22H374/components/'
                    'kernelcache.release.ipad7c.decompressed/'
                    'kernelcache.release.ipad7c.decompressed')
DEFAULT_BINDINGS = ROOT / 'mac-build/native-ufs/bindings.json'
DEFAULT_OUTPUT = ROOT.parent / 'thor-out/kernel/kernel-ipados18-thor.macho'
DEFAULT_PLAN = ROOT.parent / 'thor-out/kernel/prelink-thor.json'

ROOTS = [
    'com.apple.kec.corecrypto',
    'com.apple.kec.pthread',
    'com.apple.filesystems.apfs',
    'com.apple.kec.Compression',
    'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',
]
LIBRARY = {
    'com.apple.driver.AppleARMWatchdogTimer',
    'com.apple.driver.AppleARMPlatform',
    'com.apple.driver.ApplePMGR',
    'com.apple.driver.AppleEffaceableStorage',
    'com.apple.driver.AppleMobileFileIntegrity',
}


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument('--native', type=Path, default=DEFAULT_NATIVE)
  ap.add_argument('--original', type=Path, default=DEFAULT_ORIGINAL)
  ap.add_argument('--bindings', type=Path, default=DEFAULT_BINDINGS)
  ap.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
  ap.add_argument('--plan-json', type=Path, default=DEFAULT_PLAN)
  args = ap.parse_args()

  native = args.native.read_bytes()
  orig = arm64_image(args.original.read_bytes())
  m = macho(orig)
  bindings = json.loads(args.bindings.read_text())
  print('bindings build:', bindings.get('build'),
        'prelink_base:', bindings.get('fixed_prelink_base'))
  base = min(s['va'] for s in m['segments']
             if s['name'] in COALESCED and s['size'])
  slide = int(bindings['fixed_prelink_base'], 16) - base
  print('base', hex(base), 'slide', hex(slide))

  closure = plan(native, orig, ROOTS, slide, verify_images=False)
  library = LIBRARY & set(closure['components'])
  print('closure components:', len(closure['components']),
        'library-only:', len(library))

  candidate, selection = emit(native, orig, ROOTS, slide,
                              verify_images=False,
                              library_only_components=library)
  args.output.write_bytes(candidate)
  args.plan_json.write_text(json.dumps(selection, indent=2))
  print('prelinked kernel:', len(candidate), 'bytes ->', args.output)
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
