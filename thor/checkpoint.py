#!/usr/bin/env python3
"""Record the sources of a working copy as the checkpoint of its version.

    python3.13 -I thor/checkpoint.py thor/versions/v248.json --work work/v248-unified

Writes, in this repository:
  patches/xnu-ipados18/checkpoints/<checkpoint>/   xnu-after-series.patch and manifest.json: everything between
                                                 "pristine Apple XNU + the recorded patch series" and the kernel
                                                 source tree of the working copy
  checkpoints/<checkpoint>/drivers/               one patch per driver that differs from the commit its upstream
                                                 repository pins, and a manifest

The version file names the checkpoint.  It is verified as it is made: the kernel patch round-trips to the recorded
tree hash on a fresh replay, and each driver patch applies to a fresh export of its pinned commit and equals the
working tree file for file.  Generated payload headers (they embed signed libraries or name local paths) and the
backup and reject files of the patch tool stay out.  bootstrap.py builds working copies from a checkpoint.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = REPO.parent.parent
PONG, WORK = ROOT / 'src/UniXNU-pong', ROOT / 'work'
SERIES_DIR = REPO / 'patches/xnu-ipados18'
XNU = PONG / 'refer/ipados18/xnu-11417.140.69'
GITENV = dict(os.environ, GIT_AUTHOR_NAME='checkpoint', GIT_AUTHOR_EMAIL='checkpoint@localhost',
              GIT_COMMITTER_NAME='checkpoint', GIT_COMMITTER_EMAIL='checkpoint@localhost')
GENERATED = re.compile(r'bsd/kern/thor_[a-z0-9_]*payload[a-z0-9_]*\.h')
LEFTOVER = ('.orig', '.rej')


def run(args, cwd=None, env=None, check=True):
    r = subprocess.run([str(a) for a in args], cwd=cwd and str(cwd), env=env or GITENV, capture_output=True)
    if check and r.returncode:
        raise SystemExit(f'failed ({r.returncode}): {args[:5]}\n{r.stdout[-1500:].decode(errors="replace")}\n{r.stderr[-1500:].decode(errors="replace")}')
    return r


def out(args, cwd=None, env=None):
    return run(args, cwd, env).stdout.decode().strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')


def files_under(root, skip=('.git',)):
    res = []
    for base, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if not (base == root and d in skip)]
        res += [os.path.relpath(os.path.join(base, f), root) for f in fs]
    return sorted(res)


def git_repo(path):
    if not os.path.exists(path / '.git'):
        run(['git', 'init', '-q'], path)
    run(['git', 'add', '-A', '.'], path)
    run(['git', 'commit', '-q', '--allow-empty', '-m', 'base'], path)


def series():
    return [l.strip() for l in (SERIES_DIR / 'series').read_text().splitlines() if l.strip() and not l.startswith('#')]


# ---------------------------------------------------------------------------------------------- kernel
def replay(scratch, upstream):
    """pristine XNU with the recorded series: the tree every checkpoint is measured from (kept, it never changes)"""
    base = scratch / 'replay'
    if not os.path.isdir(base):
        os.makedirs(base)
        run(['bash', '-c', f'git -C "{XNU}" archive {upstream} | tar -x -C "{base}"'])
        for name in series():
            run(['patch', '--batch', '--fuzz=3', '--forward', '-p1', '-i', SERIES_DIR / name], cwd=base, check=False)
        for p in Path(base).rglob('*'):
            if p.is_file() and p.name.endswith(LEFTOVER):
                p.unlink()
        git_repo(base)
    return base, out(['git', 'rev-parse', 'HEAD^{tree}'], base)


def kernel(version, work, scratch, previous, upstream):
    name = version['checkpoint']
    target = SERIES_DIR / 'checkpoints' / name
    tree = work / 'build-tree/.cache/xnu-work'
    if not (tree / '.git').exists():
        raise SystemExit(f'{tree} is not the kernel source tree of a working copy')
    before = None
    if previous:
        was = json.loads((SERIES_DIR / f'checkpoints/{previous}/manifest.json').read_text())
        before = was[next(k for k in was if re.fullmatch(r'v\d+_tree', k))]
    base, t0 = replay(scratch, upstream)
    if previous and was['replayed_tree'] != t0:
        raise SystemExit('the replay differs from the previous checkpoint: the patch series changed')
    t0_files = out(['git', 'ls-files'], base).split('\n')
    run(['git', 'reset', '-q', '--hard'], base)             # the replay is kept between runs: start from the clean tree
    if previous:
        # the previous checkpoint's tree, so that the change since then can be listed
        run(['git', 'apply', '--index', SERIES_DIR / f'checkpoints/{previous}/xnu-after-series.patch'], base)
        if out(['git', 'write-tree'], base) != before:
            raise SystemExit('the previous checkpoint does not round-trip on the replay')
    run(['git', 'reset', '-q', '--hard'], base)
    kept_out, copied, removed, present = {}, [], [], set()
    for rel in files_under(tree):
        if rel == '.git' or rel.startswith('.git/'):
            continue
        present.add(rel)
        if rel.endswith(LEFTOVER):
            kept_out[rel] = 'backup or reject file of the patch tool'
            continue
        if GENERATED.fullmatch(rel):
            kept_out[rel] = 'generated: embeds a signed library as a byte array or names local library paths'
            continue
        data = (tree / rel).read_bytes()
        dst = base / rel
        if dst.exists() and dst.read_bytes() == data and (dst.stat().st_mode & 0o111) == ((tree / rel).stat().st_mode & 0o111):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tree / rel, dst)
        copied.append(rel)
    for rel in t0_files:
        if rel not in present:
            (base / rel).unlink()
            removed.append(rel)
    run(['git', 'add', '-A', '.'], base)
    tree_hash = out(['git', 'write-tree'], base)
    patch = run(['git', 'diff', '--cached', '--no-color', '--full-index'], base).stdout
    if b'Binary files' in patch or b'/Users/' in patch:
        raise SystemExit('binary content or a local path would go into the checkpoint patch')
    files = [f for f in out(['git', 'diff', '--cached', '--name-only', '--no-color'], base).split('\n') if f]
    listing = out(['git', 'ls-files'], base).split('\n')
    stat = out(['git', 'diff-tree', '-r', '--numstat', before, tree_hash], base).split('\n') if before else []
    target.mkdir(parents=True, exist_ok=True)
    (target / 'xnu-after-series.patch').write_bytes(patch)
    # the round trip: the clean replay with the patch is the same tree
    run(['git', 'reset', '-q', '--hard'], base)
    run(['git', 'apply', '--index', target / 'xnu-after-series.patch'], base)
    if out(['git', 'write-tree'], base) != tree_hash or out(['git', 'ls-files'], base).split('\n') != listing:
        raise SystemExit('the checkpoint patch does not round-trip')
    if sorted(listing) != sorted(present - set(kept_out)):
        raise SystemExit('the file list differs from the kernel source tree')
    change = [dict(file=l.split('\t')[2], added=int(l.split('\t')[0]), removed=int(l.split('\t')[1])) for l in stat if l]
    names = series()
    dump(target / 'manifest.json', dict(
        checkpoint=f'{name}-xnu-tree',
        kernel='Apple XNU xnu-11417.140.69, the tree iPadOS 18.7.10 / 22H374 (Darwin 24) is built from',
        upstream_commit=upstream,
        series=f"../../series ({len(names)} entries, applied with patch --batch --fuzz=3 --forward -p1)",
        replayed_tree=t0, **{f"v{version['version']}_tree": tree_hash},
        patch=dict(path='xnu-after-series.patch', sha256=sha(patch), bytes=len(patch), files=files),
        copied_from_work_tree=copied, removed_from_replay=removed, kept_out=kept_out,
        build_tree=dict(files=len(present), in_the_tree=len(listing), source='build-tree/.cache/xnu-work of the working copy'),
        **({f'since_{previous}': dict(files=len(change), change=change)} if previous else {})))
    return dict(tree=tree_hash, patch_files=len(files), bytes=len(patch), kept_out=len(kept_out),
                files_changed_since_previous=len(change))


# ---------------------------------------------------------------------------------------------- drivers
def drivers(version, previous):
    """one patch per driver that differs from the commit its repository pins"""
    name = version['checkpoint']
    target = REPO / 'checkpoints' / name / 'drivers'
    old_path = REPO / f'checkpoints/{previous}/drivers/manifest.json'
    old = json.loads(old_path.read_text()) if old_path.exists() else {}
    result = {}
    for driver in sorted(os.listdir(PONG / 'drivers')):
        repo = PONG / 'drivers' / driver
        if not (repo / '.git').exists():
            continue
        commit = out(['git', 'rev-parse', 'HEAD'], repo)
        index = WORK / f'.index-{driver}'
        env = dict(GITENV, GIT_INDEX_FILE=str(index))
        run(['git', 'read-tree', 'HEAD'], repo, env)
        run(['git', 'add', '-A', '--', '.', ':!*.orig', ':!*.rej', ':!.DS_Store'], repo, env)
        absent = []
        deleted = out(['git', 'diff', '--cached', '--name-only', '--diff-filter=D'], repo, env).split('\n')
        for rel in [d for d in deleted if d]:       # upstream files the working tree no longer carries stay out of the patch
            run(['git', 'reset', '-q', 'HEAD', '--', rel], repo, env)
            absent.append(rel)
        patch = run(['git', 'diff', '--cached', '--no-color', '--full-index'], repo, env).stdout
        status = [l for l in out(['git', 'diff', '--cached', '--name-status', '--no-color'], repo, env).split('\n') if l]
        index.unlink(missing_ok=True)
        if not patch:
            continue
        if b'Binary files' in patch or b'/Users/' in patch:
            raise SystemExit(f'{driver}: binary content or a local path would go into the checkpoint patch')
        target.mkdir(parents=True, exist_ok=True)
        (target / f'{driver}.patch').write_bytes(patch)
        # the round trip: a fresh export of the pinned commit with the patch equals the working tree
        fresh = WORK / f'.export-{driver}'
        shutil.rmtree(fresh, ignore_errors=True)
        fresh.mkdir()
        run(['bash', '-c', f'git -C "{repo}" archive {commit} | tar -x -C "{fresh}"'])
        git_repo(fresh)
        run(['git', 'apply', '--index', target / f'{driver}.patch'], fresh)
        left_out = []
        for rel in files_under(repo):
            if rel == '.git' or (repo / rel).is_dir():
                continue
            if rel.endswith(LEFTOVER):
                left_out.append(rel)
                continue
            if (fresh / rel).read_bytes() != (repo / rel).read_bytes():
                raise SystemExit(f'{driver}: {rel} is not reproduced by its patch')
        shutil.rmtree(fresh)
        result[driver] = dict(upstream_repository=old.get(driver, {}).get('upstream_repository') or
                              run(['git', 'remote', 'get-url', 'origin'], repo, check=False).stdout.decode().strip(),
                              upstream_commit=commit, patch=f'{driver}.patch', sha256=sha(patch), bytes=len(patch),
                              files=[l.split('\t')[1] for l in status], new_files=[l.split('\t')[1] for l in status if l.startswith('A\t')],
                              upstream_files_absent_from_the_working_tree=absent, left_out=left_out,
                              **({'unchanged_since_' + previous: sha(patch) == old[driver]['sha256']} if driver in old else {}))
    dump(target / 'manifest.json', result)
    return {d: dict(bytes=v['bytes'], files=len(v['files'])) for d, v in result.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('version', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--previous', help='the checkpoint before this one (default: thor-v<version - 1>)')
    parser.add_argument('--kernel-only', action='store_true')
    parser.add_argument('--drivers-only', action='store_true')
    args = parser.parse_args()
    version = json.loads(args.version.read_text())
    work = args.work.resolve()
    if not (work / 'recipe.json').exists():
        raise SystemExit(f'{work} was not made by bootstrap.py')
    upstream = out(['git', 'rev-parse', 'HEAD'], XNU)
    previous = args.previous or f"thor-v{version['version'] - 1}"
    scratch = work / '.checkpoint'
    scratch.mkdir(exist_ok=True)
    report = dict(checkpoint=version['checkpoint'])
    if not args.drivers_only:
        report['kernel'] = kernel(version, work, scratch, previous, upstream)
    if not args.kernel_only:
        report['drivers'] = drivers(version, previous)
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
