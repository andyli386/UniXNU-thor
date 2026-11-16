#!/usr/bin/env python3
"""Sparse prelink injection for the thor bare kernel (iPadOS 18.7.10 / 22H374).

Extracts the Apple system kext closure the bare kernel's kext references need
(fixed prelink VA 0xfffffff00b000000) from pong's decompressed original
kernelcache and emits the bootable prelinked kernel.

Without this step the bare kernel panics in OSMetaClass::postModLoad (dangling
pointers into the prelink region). Inputs:
  - native:   thor bare kernel from build-ipados18-pong-macos.py
              (mach.development.pong)
  - original: locally extracted and decompressed 22H374 kernel cache
  - bindings: locally generated 22H374 storage ABI bindings
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
GPU_ROOT = 'com.apple.iokit.IOGPUFamily'
GPU_LIBRARY = {
    GPU_ROOT,
    'com.apple.iokit.IOSurface',
    'com.apple.iokit.IOReportFamily',
    'com.apple.iokit.IOSlowAdaptiveClockingFamily',
}


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument('--native', type=Path, required=True)
  ap.add_argument('--original', type=Path, required=True)
  ap.add_argument('--bindings', type=Path, required=True)
  ap.add_argument('--output', type=Path, required=True)
  ap.add_argument('--plan-json', type=Path, required=True)
  ap.add_argument('--gpu', action='store_true', help='include the matching 22H374 IOGPUFamily bindings')
  args = ap.parse_args()

  native = args.native.read_bytes()
  orig = arm64_image(args.original.read_bytes())
  m = macho(orig)
  bindings = json.loads(args.bindings.read_text())
  if bindings.get('build') != '22H374':
    raise ValueError('Thor prelink requires 22H374 storage bindings')
  print('bindings build:', bindings.get('build'),
        'prelink_base:', bindings.get('fixed_prelink_base'))
  base = min(s['va'] for s in m['segments']
             if s['name'] in COALESCED and s['size'])
  slide = int(bindings['fixed_prelink_base'], 16) - base
  print('base', hex(base), 'slide', hex(slide))

  roots = [*ROOTS, GPU_ROOT] if args.gpu else ROOTS
  libraries = LIBRARY | GPU_LIBRARY if args.gpu else LIBRARY
  closure = plan(native, orig, roots, slide, verify_images=False)
  if args.gpu and GPU_ROOT not in closure['components']:
    raise ValueError('Matching 22H374 IOGPUFamily is absent from the prelink closure')
  library = libraries & set(closure['components'])
  print('closure components:', len(closure['components']),
        'library-only:', len(library))

  candidate, selection = emit(native, orig, roots, slide,
                              verify_images=False,
                              library_only_components=library)
  args.output.write_bytes(candidate)
  args.plan_json.write_text(json.dumps(selection, indent=2))
  print('prelinked kernel:', len(candidate), 'bytes ->', args.output)
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
