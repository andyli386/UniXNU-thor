#!/usr/bin/env python3
"""Use of the 16 KiB boot stack (INTSTACK_SIZE) by the two start-up qsort() calls.

The boot CPU runs arm_init() -> kernel_bootstrap() -> ... -> kalloc_init() on the static
interrupt stack.  bsd/kern/qsort.c recurses once per left partition.  This adds the frames
below qsort, the deepest recursion and the leaf called from the innermost frame.
usage: boot_stack_budget.py <macho.unstripped> <startup-table-depth> <kalloc-view-depth>
       (depths come from startup_sort_sim.py and kalloc_orderings.py)
"""
import re, subprocess, sys
path, d_start, d_kalloc = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
objdump = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True).stdout.strip()
syms = {}
for l in subprocess.run(['xcrun', 'nm', '-n', '-arch', 'arm64', path], capture_output=True, text=True).stdout.splitlines():
    p = l.split()
    if len(p) >= 3:
        try:
            syms.setdefault(' '.join(p[2:]), int(p[0], 16))
        except ValueError:
            pass

def frame(name):
    """Stack bytes the prologue reserves before the first call/branch."""
    a = syms[name]
    out = subprocess.run([objdump, '-d', '--arch=arm64', '--no-show-raw-insn', '--start-address=0x%x' % a,
                          '--stop-address=0x%x' % (a + 0x100), path], capture_output=True, text=True).stdout
    total = 0
    for l in out.splitlines():
        m = re.match(r'^\s*[0-9a-f]+:\s+(\S+)\s*(.*)$', l)
        if not m or m.group(1).endswith('>:'):
            continue
        mn, ops = m.group(1), m.group(2)
        if mn == 'stp' and '[sp, #-0x' in ops and ops.endswith(']!'):
            total += int(re.search(r'#-(0x[0-9a-f]+)\]!', ops).group(1), 16)
        elif mn == 'sub' and ops.startswith('sp, sp, #'):
            total += int(ops.split('#')[1].split(';')[0].strip(), 16)
        if mn in ('bl', 'blr', 'b', 'br', 'ret'):
            break
    return total

def one(prefix, default=None):
    hit = [k for k in syms if prefix in k]
    assert default is not None or len(hit) == 1, (prefix, hit)
    return hit[0] if hit else default

view_init = '_kalloc_type_view_init_fixed'
entry_cmp, = [k for k in syms if 'startup_entry_cmp' in k and '.__desc' not in k]
kalloc_chain = ['_arm_init', '_kernel_bootstrap', '_vm_mem_bootstrap', '_kernel_startup_initialize_upto',
                '_kalloc_init', view_init]
startup_chain = ['_arm_init', '_kernel_startup_bootstrap', entry_cmp]
T = 0x4000
fq, fs = frame('_qsort'), frame('_swapfunc') if '_swapfunc' in syms else 0
comparator = frame('_kalloc_type_cmp_fixed')
print('%s: qsort frame 0x%x, swapfunc frame 0x%x, comparator frame 0x%x, boot stack 0x%x' % (
    path.split('/')[-3], fq, fs, comparator, T))
for name, chain, depth in (('startup table sort', startup_chain, d_start), ('kalloc-type view sort', kalloc_chain, d_kalloc)):
    fixed = sum(frame(f) for f in chain)
    # Deepest point: every one of the `depth` qsort frames is a full frame, and the innermost one
    # still calls the comparator (or the swap leaf, which replaces it as the deepest callee).
    worst = fixed + depth * fq + max(fs, comparator)
    print('  %-22s depth %2d  frames below qsort 0x%03x  worst 0x%04x of 0x4000  free %5d B  %s' % (
        name, depth, fixed, worst, T - worst, 'OK' if worst < T else 'OVERFLOW'))
