#!/usr/bin/env python3
"""Check the linked scheduler, sandbox and GPU paths used by Thor v156."""
import argparse
from pathlib import Path
import re
import subprocess


def disassemble(kernel, symbol):
    return subprocess.check_output(
        ['xcrun', 'llvm-objdump', f'--disassemble-symbols={symbol}', kernel],
        text=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', required=True, type=Path, help='linked, unprelinked Thor Mach-O')
    args = parser.parse_args()
    symbols = subprocess.check_output(['nm', '-j', args.kernel], text=True).splitlines()
    for required in ('_sched_edge_dispatch', '_pong_cpu_preferred_idle_mask'):
        if required not in symbols:
            raise ValueError(f'missing linked scheduler symbol {required}')

    sandbox = disassemble(args.kernel, '_minisandbox_policy_syscall').splitlines()
    calls = [i for i, line in enumerate(sandbox) if re.search(r'\bbl\s+.*<_copyinstr>', line)]
    if len(calls) != 3:
        raise ValueError(f'expected 3 sandbox copyinstr calls, got {len(calls)}')
    for i in calls:
        writes = [line for line in sandbox[max(0, i - 7):i] if re.search(r'\bx3,', line)]
        if not writes or not re.search(r'\b(?:add|sub)\s+x3,\s*(?:sp|x29),', writes[-1]):
            raise ValueError('sandbox copyinstr must take a stack length output')

    targets = [symbol for symbol in symbols
               if re.fullmatch(r'__ZN18NativeAdrenoMemory13prepareSystemE\w+', symbol)]
    if len(targets) != 1:
        raise ValueError(f'expected one GPU page-preparation symbol, got {len(targets)}')
    gpu = disassemble(args.kernel, targets[0])
    for expected in ('NativeAdrenoMaintainPhysicalSpans', 'NativeAdrenoReassertWriteCombined'):
        if not re.search(r'\bbl\s+[^\n]+<[^>]*' + expected, gpu):
            raise ValueError(f'linked GPU page preparation does not call {expected}')
    if 'NativeAdrenoMaintainPhysicalPages' in gpu:
        raise ValueError('linked GPU page preparation uses the previous per-span maintenance call')
    print('Verified Thor v156 linked scheduler, sandbox and GPU paths')


if __name__ == '__main__':
    main()
