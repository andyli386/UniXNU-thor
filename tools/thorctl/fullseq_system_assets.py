#!/usr/bin/env python3
"""Prepare official 20G81 host inputs; never discover or operate a phone.

The fixed restore identity comes from the current Pong board source.  Source
members are fetched with strict HTTP ranges, then ZIP size/CRC and local hashes
are checked.  Source disks, host readability and a Thor runtime root are distinct
claims.  No path in this module denotes a phone partition.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import threading
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
import zipfile
import zlib

URL = ('https://updates.cdn-apple.com/2023SummerFCS/fullrestores/042-44385/'
       '366890C4-D9B5-4938-B399-C19FB58DBEE0/'
       'iPad_Pro_HFR_16.6.1_20G81_Restore.ipsw')
ARCHIVE_BYTES = 5592869403
CHUNK = 8 << 20
IDENTITY = dict(product='iPad7,1', version='16.6.1', build='20G81',
                device_class='j120ap', chip_id=0x8011, board_id=12)
COMPONENTS = ('StaticTrustCache', 'Cryptex1,SystemTrustCache',
              'Cryptex1,AppTrustCache', 'SystemVolume',
              'Cryptex1,SystemVolume', 'Cryptex1,AppVolume',
              'Cryptex1,AppOS', 'Ap,SystemVolumeCanonicalMetadata',
              'Cryptex1,SystemOS', 'OS')


class AssetError(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def hash_file(path, algorithm='sha256'):
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as source:
        while block := source.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    with temporary.open('w') as target:
        json.dump(value, target, indent=2, sort_keys=True)
        target.write('\n')
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def check_range(status, content_range, actual_bytes, start, end, total):
    if status != 206 or content_range != f'bytes {start}-{end}/{total}':
        raise AssetError('HTTP response is not the exact requested archive range')
    if actual_bytes != end - start + 1:
        raise AssetError('short or oversized HTTP range response')


def fetch_range(start, end, *, url=URL, total=ARCHIVE_BYTES, retries=4):
    if not (0 <= start <= end < total):
        raise AssetError('archive range outside fixed source')
    error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={
                'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(end - start + 2)
                check_range(response.status, response.headers.get('Content-Range'),
                            len(data), start, end, total)
            return data
        except (OSError, urllib.error.URLError, AssetError) as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 8))
    raise AssetError(f'archive range {start}..{end} unavailable: {error}')


class RemoteArchive(io.RawIOBase):
    """Small-cache, strict range reader for ZIP directory and metadata only."""
    def __init__(self):
        self.position = 0
        self.block = 1 << 18
        self.cache = {}

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence == 0:
            position = offset
        elif whence == 1:
            position = self.position + offset
        elif whence == 2:
            position = ARCHIVE_BYTES + offset
        else:
            raise ValueError('invalid seek mode')
        if position < 0:
            raise AssetError('negative archive position')
        self.position = position
        return position

    def readinto(self, target):
        remaining = min(len(target), max(0, ARCHIVE_BYTES - self.position))
        done = 0
        while done < remaining:
            index, inside = divmod(self.position, self.block)
            if index not in self.cache:
                start = index * self.block
                self.cache[index] = fetch_range(start, min(start + self.block, ARCHIVE_BYTES) - 1)
                if len(self.cache) > 8:
                    self.cache.pop(next(iter(self.cache)))
            block = self.cache[index]
            count = min(remaining - done, len(block) - inside)
            target[done:done + count] = block[inside:inside + count]
            done += count
            self.position += count
        return done


def select_identity(manifest):
    if (manifest.get('ProductVersion'), manifest.get('ProductBuildVersion')) != ('16.6.1', '20G81'):
        raise AssetError('restore version/build mismatch')
    if 'iPad7,1' not in manifest.get('SupportedProductTypes', []):
        raise AssetError('restore product mismatch')
    selected = []
    for index, item in enumerate(manifest.get('BuildIdentities', [])):
        info = item.get('Info', {})
        if (info.get('DeviceClass') == 'j120ap' and info.get('RestoreBehavior') == 'Erase'
                and int(item.get('ApChipID', '0'), 16) == 0x8011
                and int(item.get('ApBoardID', '0'), 16) == 12):
            selected.append((index, item))
    if len(selected) != 1:
        raise AssetError('expected one exact Erase build identity')
    return selected[0]


def safe_basename(member):
    path = PurePosixPath(member)
    if path.is_absolute() or '..' in path.parts or not path.name or '\\' in member:
        raise AssetError('unsafe ZIP member name')
    return path.name


def inventory(archive, manifest_raw, components=COMPONENTS):
    manifest = plistlib.loads(manifest_raw)
    index, identity = select_identity(manifest)
    rows, names = [], set()
    for component in components:
        value = identity['Manifest'].get(component)
        if value is None:
            raise AssetError(f'component absent from selected identity: {component}')
        member = value['Info']['Path']
        basename = safe_basename(member)
        if basename in names:
            raise AssetError('selected component destination collision')
        names.add(basename)
        info = archive.getinfo(member)
        if info.flag_bits & 1 or info.compress_type not in (0, 8):
            raise AssetError('unsupported or encrypted ZIP member')
        archive.fp.seek(info.header_offset)
        header = archive.fp.read(30)
        if len(header) != 30 or header[:4] != b'PK\x03\x04':
            raise AssetError('invalid ZIP local header')
        flags, method = struct.unpack_from('<HH', header, 6)
        length, extra = struct.unpack_from('<HH', header, 26)
        raw_name = archive.fp.read(length)
        expected_name = member.encode('utf-8' if flags & 0x800 else 'cp437')
        if raw_name != expected_name or method != info.compress_type or flags != info.flag_bits:
            raise AssetError('ZIP local/central metadata disagreement')
        offset = info.header_offset + 30 + length + extra
        if offset < 0 or offset + info.compress_size > ARCHIVE_BYTES:
            raise AssetError('member data exceeds archive')
        digest = value.get('Digest')
        rows.append(dict(component=component, member=member, basename=basename,
                         bytes=info.file_size, compressed_bytes=info.compress_size,
                         method=method, zip_crc32=f'{info.CRC:08x}', data_offset=offset,
                         manifest_digest_hex=digest.hex() if isinstance(digest, bytes) else None,
                         manifest_hash_method=value['Info'].get('HashMethod')))
    return dict(schema='thor-fullseq01-official-system-assets-inventory-v1',
                created_at=utc(), source_url=URL, archive_bytes=ARCHIVE_BYTES,
                build_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                identity=IDENTITY, identity_index=index, components=rows,
                phone_actions=0, whole_ipsw_downloaded=False)


def local_measure(path, row):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != row['bytes']:
        return None
    crc = 0
    sha, sha384 = hashlib.sha256(), hashlib.sha384()
    with path.open('rb') as source:
        while block := source.read(CHUNK):
            crc = zlib.crc32(block, crc)
            sha.update(block)
            sha384.update(block)
    if f'{crc & 0xffffffff:08x}' != row['zip_crc32']:
        raise AssetError(f'local member CRC mismatch: {path}')
    return dict(path=str(path.resolve()), bytes=path.stat().st_size,
                sha256=sha.hexdigest(), sha384=sha384.hexdigest(),
                zip_crc32=f'{crc & 0xffffffff:08x}', zip_crc_checked=True)


def group_missing_ranges(pending, width=8):
    """Coalesce only adjacent missing 8MiB chunks; preserve resume hash units."""
    groups = []
    for index in pending:
        if groups and index == groups[-1][-1] + 1 and len(groups[-1]) < width:
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def download_compressed(row, destination, *, workers=4):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    state_path = destination.with_name(destination.name + '.ranges.json')
    identity = {k: row[k] for k in ('member', 'compressed_bytes', 'data_offset', 'zip_crc32')}
    state = dict(schema='thor-official-member-range-download-v1', source_url=URL,
                 archive_bytes=ARCHIVE_BYTES, source_member=identity, chunk_bytes=CHUNK,
                 completed={})
    if state_path.exists():
        old = json.loads(state_path.read_text())
        if any(old.get(k) != state[k] for k in ('schema', 'source_url', 'archive_bytes', 'source_member', 'chunk_bytes')):
            raise AssetError('partial member has different source identity')
        state = old
    elif destination.exists():
        raise AssetError('untracked partial member already exists')
    descriptor = os.open(destination, os.O_RDWR | os.O_CREAT, 0o600)
    os.ftruncate(descriptor, row['compressed_bytes'])
    lock = threading.Lock()
    count = (row['compressed_bytes'] + CHUNK - 1) // CHUNK
    pending = []
    for index in range(count):
        start = index * CHUNK
        size = min(CHUNK, row['compressed_bytes'] - start)
        known = state['completed'].get(str(index))
        if known and known['bytes'] == size and hashlib.sha256(os.pread(descriptor, size, start)).hexdigest() == known['sha256']:
            continue
        pending.append(index)
    last_print = time.monotonic()

    def get(group):
        nonlocal last_print
        relative = group[0] * CHUNK
        size = min((group[-1] + 1) * CHUNK, row['compressed_bytes']) - relative
        start = row['data_offset'] + relative
        data = fetch_range(start, start + size - 1)
        written = os.pwrite(descriptor, data, relative)
        if written != size:
            raise AssetError('short local range write')
        records = {}
        for index in group:
            inside = (index - group[0]) * CHUNK
            block = data[inside:inside + CHUNK]
            records[str(index)] = dict(bytes=len(block), sha256=hashlib.sha256(block).hexdigest())
        with lock:
            state['completed'].update(records)
            write_json(state_path, state)
            if time.monotonic() - last_print > 10 or len(state['completed']) == count:
                print(json.dumps(dict(event='download_progress', member=row['member'],
                                      chunks=len(state['completed']), total_chunks=count,
                                      compressed_bytes=sum(v['bytes'] for v in state['completed'].values()))), flush=True)
                last_print = time.monotonic()
        return records

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(get, group_missing_ranges(pending)):
                pass
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return dict(path=str(destination.resolve()), bytes=destination.stat().st_size,
                sha256=hash_file(destination), range_state=str(state_path.resolve()),
                range_state_sha256=hash_file(state_path))


def extract_member(row, compressed, destination):
    destination = Path(destination)
    if destination.exists():
        measured = local_measure(destination, row)
        if measured is None:
            raise AssetError('existing destination has unexpected size')
        return measured
    temporary = destination.with_name(destination.name + '.extracting')
    if temporary.exists():
        raise AssetError('unreviewed extraction intermediate exists')
    decoder = zlib.decompressobj(-15) if row['method'] == 8 else None
    crc, written = 0, 0
    sha, sha384 = hashlib.sha256(), hashlib.sha384()
    def output_block(target, data):
        nonlocal written, crc
        if written + len(data) > row['bytes']:
            raise AssetError('decompressed member exceeds declared size')
        target.write(data)
        written += len(data)
        crc = zlib.crc32(data, crc)
        sha.update(data)
        sha384.update(data)

    with Path(compressed).open('rb') as source, temporary.open('xb') as target:
        while block := source.read(CHUNK):
            if decoder:
                pending = block
                while pending:
                    output_block(target, decoder.decompress(pending, CHUNK))
                    pending = decoder.unconsumed_tail
            else:
                output_block(target, block)
        if decoder:
            tail = decoder.flush()
            output_block(target, tail)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise AssetError('incomplete or trailing compressed ZIP stream')
        target.flush()
        os.fsync(target.fileno())
    if written != row['bytes'] or f'{crc & 0xffffffff:08x}' != row['zip_crc32']:
        raise AssetError('extracted member size/CRC mismatch')
    os.replace(temporary, destination)
    return dict(path=str(destination.resolve()), bytes=written, sha256=sha.hexdigest(),
                sha384=sha384.hexdigest(), zip_crc32=f'{crc & 0xffffffff:08x}', zip_crc_checked=True)


def prepare(root, evidence, output, *, workers=4, inventory_only=False):
    root, evidence, output = map(lambda p: Path(p).resolve(), (root, evidence, output))
    producer_sha256 = hash_file(__file__)
    if not output.is_relative_to(root) or not evidence.is_relative_to(root):
        raise AssetError('all asset outputs must stay inside the project')
    evidence.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output).free
    archive = zipfile.ZipFile(RemoteArchive())
    manifest_raw = archive.read('BuildManifest.plist')
    inv = inventory(archive, manifest_raw)
    manifest_path = evidence / 'BuildManifest.plist'
    if manifest_path.exists() and manifest_path.read_bytes() != manifest_raw:
        raise AssetError('previous BuildManifest bytes differ')
    manifest_path.write_bytes(manifest_raw)
    write_json(evidence / 'system-assets-inventory.json', inv)
    if inventory_only:
        return inv
    needed = sum(x['compressed_bytes'] + x['bytes'] for x in inv['components'])
    if free < needed + (8 << 30):
        raise AssetError('insufficient free disk for official members and bounded workspace')
    existing = root / 'out/ios16-official-restore-20G81'
    records = []
    for row in inv['components']:
        dest = output / row['basename']
        source = dest if dest.exists() else existing / row['basename']
        measured = local_measure(source, row)
        compressed = None
        if measured is None:
            compressed = download_compressed(row, output / '.downloads' / (row['basename'] + '.zip-member'), workers=workers)
            measured = extract_member(row, compressed['path'], dest)
        record = {**row, **measured, 'compressed_input': compressed,
                  'source_kind': 'official_restore_member', 'runtime_root_qualified': False}
        if row['manifest_hash_method'] == 'sha2-384':
            record['manifest_sha384_matches_file'] = measured['sha384'] == row['manifest_digest_hex']
        records.append(record)
        write_json(evidence / 'system-assets-fetch-progress.json', dict(
            schema='thor-fullseq01-system-assets-fetch-progress-v1', created_at=utc(),
            completed=records, expected_components=list(COMPONENTS), phone_actions=0))
        print(json.dumps(dict(event='member_ready', component=row['component'],
                             path=measured['path'], bytes=measured['bytes'], sha256=measured['sha256'])), flush=True)
    result = dict(schema='thor-fullseq01-official-system-assets-v1', created_at=utc(),
                  result='PASS_OFFICIAL_RESTORE_MEMBERS_HOST_ONLY', identity=IDENTITY,
                  producer_path=str(Path(__file__).resolve()), producer_sha256=producer_sha256,
                  source_url=URL, archive_bytes=ARCHIVE_BYTES,
                  build_manifest_path=str(manifest_path),
                  build_manifest_sha256=hash_file(manifest_path),
                  disk_free_before=free, disk_free_after=shutil.disk_usage(output).free,
                  inputs=records, whole_ipsw_downloaded=False, phone_actions=0,
                  thor_root_media_qualified=False, runtime_mount_proven=False,
                  boot_eligible=False, ios_home_screen_proven=False,
                  remaining=['raw OS decode and read-only APFS readability',
                             'host Data construction and exact UUID/interface inventory',
                             'Thor root backing medium and runtime mount qualification'])
    write_json(evidence / 'system-assets-fetch.json', result)
    return result


def der_item(data, offset, limit=None):
    limit = len(data) if limit is None else limit
    if offset < 0 or offset + 2 > limit:
        raise AssetError('truncated DER item')
    tag, length = data[offset], data[offset + 1]
    start = offset + 2
    if length & 0x80:
        width = length & 0x7f
        if width == 0 or width > 4 or start + width > limit or data[start] == 0:
            raise AssetError('invalid DER length')
        length = int.from_bytes(data[start:start + width], 'big')
        start += width
        if length < 128:
            raise AssetError('noncanonical DER length')
    end = start + length
    if end > limit:
        raise AssetError('DER item exceeds enclosing data')
    return tag, start, end


def im4p_payload(data, expected_type):
    tag, start, end = der_item(data, 0)
    if tag != 0x30 or end != len(data):
        raise AssetError('expected a complete IM4P DER sequence')
    fields = []
    while start < end:
        field_tag, body, following = der_item(data, start, end)
        fields.append((field_tag, data[body:following]))
        start = following
    if (len(fields) != 4 or fields[0] != (0x16, b'IM4P')
            or fields[1] != (0x16, expected_type) or fields[2][0] != 0x16
            or fields[3][0] != 4):
        raise AssetError('IM4P fields/type differ from selected restore component')
    return fields[3][1]


def validate_trustcache(data):
    if len(data) < 24 or struct.unpack_from('<I', data)[0] != 2:
        raise AssetError('Darwin22 loader requires trust cache v2')
    entries = struct.unpack_from('<I', data, 20)[0]
    if len(data) != 24 + entries * 24:
        raise AssetError('trust cache size/count disagreement')
    previous = None
    for offset in range(24, len(data), 24):
        cdhash, kind = data[offset:offset + 20], data[offset + 20]
        if not 1 <= kind <= 4 or (previous is not None and cdhash <= previous):
            raise AssetError('trust cache hash type/order invalid')
        previous = cdhash
    return dict(version=2, entries=entries, uuid_bytes=data[4:20].hex(),
                bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def derived_loader_inputs(records, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    by_component = {record['component']: record for record in records}
    parts, caches = [], []
    for component, kind in (('StaticTrustCache', b'trst'),
                            ('Cryptex1,SystemTrustCache', b'trcs'),
                            ('Cryptex1,AppTrustCache', b'trca')):
        source = by_component[component]
        raw = Path(source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise AssetError('trust cache input changed after official extraction')
        payload = im4p_payload(raw, kind)
        meta = validate_trustcache(payload)
        destination = output / (component.replace(',', '-') + '.trustcache-v2')
        if destination.exists() and destination.read_bytes() != payload:
            raise AssetError('derived trust cache already exists with other bytes')
        destination.write_bytes(payload)
        parts.append(payload)
        caches.append(dict(component=component, source_path=source['path'],
                           source_sha256=source['sha256'], path=str(destination), **meta))
    position = 4 + 4 * len(parts)
    offsets = []
    for part in parts:
        if position & 3:
            raise AssetError('trust cache bundle offset is not aligned')
        offsets.append(position)
        position += len(part)
    bundle = struct.pack('<I', len(parts)) + struct.pack('<' + 'I' * len(parts), *offsets) + b''.join(parts)
    path = output / '20G81-loader-trustcaches.bin'
    if path.exists() and path.read_bytes() != bundle:
        raise AssetError('loader trust cache bundle already differs')
    path.write_bytes(bundle)
    auth_source = by_component['SystemVolume']
    raw = Path(auth_source['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != auth_source['sha256']:
        raise AssetError('system volume authentication source changed')
    auth = im4p_payload(raw, b'isys')
    if len(auth) != 208 or struct.unpack_from('<I', auth)[0] != 2:
        raise AssetError('unexpected 20G81 system-volume authentication payload')
    auth_path = output / 'system-volume-auth-blob.bin'
    if auth_path.exists() and auth_path.read_bytes() != auth:
        raise AssetError('system-volume authentication output differs')
    auth_path.write_bytes(auth)
    return dict(schema='thor-fullseq01-derived-loader-apple-inputs-v1',
                trustcaches=caches,
                loader_trustcache_bundle=dict(path=str(path), bytes=len(bundle),
                                             sha256=hashlib.sha256(bundle).hexdigest(),
                                             count=len(parts), offsets=offsets),
                system_volume_auth_blob=dict(path=str(auth_path), bytes=len(auth),
                                             sha256=hashlib.sha256(auth).hexdigest(),
                                             source_path=auth_source['path'],
                                             source_sha256=auth_source['sha256']),
                signature_verification_performed=False, phone_actions=0)


def plist_process(command, path, timeout=120):
    command = [str(item) for item in command]
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    Path(path).write_bytes(result.stdout)
    Path(str(path) + '.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise AssetError(f'host image operation failed ({result.returncode}): {result.stderr.decode(errors="replace").strip()}')
    return plistlib.loads(result.stdout), dict(command=command, returncode=result.returncode,
                                             stdout_path=str(path), stdout_sha256=hash_file(path),
                                             stderr_sha256=hashlib.sha256(result.stderr).hexdigest())


def image_devices(attached):
    devices = [item.get('dev-entry') for item in attached.get('system-entities', [])]
    whole = [item for item in devices if isinstance(item, str) and re.fullmatch(r'/dev/disk[0-9]+', item)]
    if not whole:
        raise AssetError('hdiutil did not return a host image disk')
    return whole[0]


def detach_image(device, evidence):
    if not re.fullmatch(r'/dev/disk[0-9]+', device):
        raise AssetError('invalid host image attachment device')
    command = ['/usr/bin/hdiutil', 'detach', device]
    result = subprocess.run(command, capture_output=True, timeout=60)
    record = dict(command=command, returncode=result.returncode,
                  stdout=result.stdout.decode(errors='replace'),
                  stderr=result.stderr.decode(errors='replace'))
    write_json(evidence, record)
    if result.returncode:
        raise AssetError('read-only host image failed to detach')
    return record


def inspect_apfs(image, tag, evidence, mount_root):
    image, evidence, mount_root = map(Path, (image, evidence, mount_root))
    before = hash_file(image)
    mount = mount_root / tag
    mount.mkdir(parents=True, exist_ok=True)
    if any(mount.iterdir()):
        raise AssetError('host image mountpoint is not empty')
    attached, attach_receipt = plist_process(
        ['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
         '-noautofsck', '-mountpoint', mount, '-plist', image], evidence / (tag + '-fresh-attach.plist'))
    device = image_devices(attached)
    try:
        mounted = [entry for entry in attached['system-entities']
                   if entry.get('mount-point') == str(mount)]
        if len(mounted) != 1:
            raise AssetError('expected one volume at the project image mountpoint')
        info, info_receipt = plist_process(['/usr/sbin/diskutil', 'info', '-plist', mounted[0]['dev-entry']],
                                           evidence / (tag + '-fresh-diskutil.plist'))
        if info.get('FilesystemType') != 'apfs' or any(info.get(key) is not False
                for key in ('Writable', 'WritableMedia', 'WritableVolume')):
            raise AssetError('host APFS attachment is not confirmed read only')
        fields = ('VolumeUUID', 'VolumeName', 'FilesystemType', 'FilesystemName',
                  'Writable', 'WritableMedia', 'WritableVolume', 'APFSContainerReference', 'TotalSize')
        content = []
        directories = ('', 'sbin', 'usr/lib', 'System/Library/CoreServices',
                       'System/Library/Caches/com.apple.dyld', 'System/Library/dyld')
        for relative in directories:
            directory = mount / relative
            if directory.is_dir() and not directory.is_symlink():
                entries = []
                for path in sorted(directory.iterdir())[:128]:
                    status = path.lstat()
                    entries.append(dict(name=path.name, bytes=status.st_size,
                                        kind='symlink' if stat.S_ISLNK(status.st_mode) else
                                        'file' if stat.S_ISREG(status.st_mode) else 'directory'))
                content.append(dict(directory=relative or '/', entries=entries))
        version = None
        version_path = mount / 'System/Library/CoreServices/SystemVersion.plist'
        if version_path.is_file() and not version_path.is_symlink():
            version = plistlib.loads(version_path.read_bytes())
        critical = []
        for relative in ('sbin/launchd', 'System/Library/CoreServices/SpringBoard.app/SpringBoard',
                         'System/Library/CoreServices/SystemVersion.plist'):
            path = mount / relative
            if path.is_file() and not path.is_symlink():
                critical.append(dict(path=relative, bytes=path.stat().st_size, sha256=hash_file(path)))
        receipt = dict(schema='thor-fullseq01-host-readonly-apfs-inspection-v1',
                       tag=tag, image_path=str(image), image_bytes=image.stat().st_size,
                       image_sha256=before, attach=attach_receipt, info=info_receipt,
                       volume={key: info.get(key) for key in fields},
                       directories=content, critical_files=critical, system_version=version,
                       host_readability_proven=True, ios16_runtime_mount_proven=False,
                       phone_actions=0)
    finally:
        detached = detach_image(device, evidence / (tag + '-fresh-detach.json'))
    after = hash_file(image)
    if before != after:
        raise AssetError('source disk changed during read-only host inspection')
    receipt['image_unchanged_after_readonly_mount'] = True
    receipt['detach'] = detached
    write_json(evidence / (tag + '-fresh-inspection.json'), receipt)
    return receipt


def decode_os_raw(image, destination, evidence):
    image, destination, evidence = map(Path, (image, destination, evidence))
    with image.open('rb') as source:
        source.seek(-512, 2)
        trailer = source.read(512)
    if trailer[:4] != b'koly':
        raise AssetError('official OS member is not the expected UDIF image')
    sectors = struct.unpack_from('>Q', trailer, 0x1ec)[0]
    raw_bytes = sectors * 512
    if not 0 < raw_bytes <= 32 << 30:
        raise AssetError('OS raw disk size outside host preparation bound')
    receipt_path = evidence / 'OS-raw-decode.json'
    if destination.exists():
        if not receipt_path.exists():
            raise AssetError('existing OS raw disk lacks provenance')
        receipt = json.loads(receipt_path.read_text())
        if (destination.stat().st_size != raw_bytes or receipt['raw_sha256'] != hash_file(destination)
                or receipt['source_sha256'] != hash_file(image)):
            raise AssetError('existing raw OS provenance differs')
        return receipt
    attached, attach_receipt = plist_process(
        ['/usr/bin/hdiutil', 'attach', '-nomount', '-readonly', '-noautofsck', '-plist', image],
        evidence / 'OS-raw-attach.plist')
    device = image_devices(attached)
    try:
        info, info_receipt = plist_process(['/usr/sbin/diskutil', 'info', '-plist', device],
                                           evidence / 'OS-raw-diskutil.plist')
        if info.get('TotalSize') != raw_bytes or info.get('WritableMedia') is not False:
            raise AssetError('host raw image size/read-only status differs from UDIF')
        temporary = destination.with_name(destination.name + '.decoding')
        if temporary.exists():
            raise AssetError('unreviewed OS raw decode intermediate exists')
        raw_device = '/dev/r' + device.removeprefix('/dev/')
        digest, copied = hashlib.sha256(), 0
        with open(raw_device, 'rb', buffering=0) as source, temporary.open('xb') as target:
            while copied < raw_bytes:
                block = source.read(min(CHUNK, raw_bytes - copied))
                if not block:
                    raise AssetError('short read from this host image attachment')
                target.write(block)
                digest.update(block)
                copied += len(block)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        receipt = dict(schema='thor-fullseq01-official-OS-native-raw-decode-v1',
                       source_path=str(image), source_sha256=hash_file(image),
                       raw_path=str(destination), raw_bytes=copied, raw_sha256=digest.hexdigest(),
                       decoder='macOS hdiutil read-only virtual disk, exact UDIF sector span',
                       attach=attach_receipt, info=info_receipt, phone_actions=0)
    finally:
        detached = detach_image(device, evidence / 'OS-raw-detach.json')
    receipt['detach'] = detached
    write_json(receipt_path, receipt)
    return receipt


def inspect_prepared(root, evidence, output):
    root, evidence, output = map(lambda value: Path(value).resolve(), (root, evidence, output))
    if not output.is_relative_to(root) or not evidence.is_relative_to(root):
        raise AssetError('all inspection outputs must stay inside the project')
    fetch_path = evidence / 'system-assets-fetch.json'
    fetched = json.loads(fetch_path.read_text())
    if fetched.get('result') != 'PASS_OFFICIAL_RESTORE_MEMBERS_HOST_ONLY' or fetched.get('identity') != IDENTITY:
        raise AssetError('official fetch report is incomplete or mismatched')
    records = fetched['inputs']
    if {record['component'] for record in records} != set(COMPONENTS):
        raise AssetError('system component set differs')
    for record in records:
        path = Path(record['path'])
        measured = local_measure(path, record)
        if not path.resolve().is_relative_to(root) or measured is None or measured['sha256'] != record['sha256']:
            raise AssetError('fetched member no longer matches official inventory')
        if record.get('manifest_hash_method') == 'sha2-384' and not record.get('manifest_sha384_matches_file'):
            raise AssetError('manifest SHA384 does not match selected file')
    inputs = derived_loader_inputs(records, output / 'loader-inputs')
    write_json(evidence / 'derived-loader-inputs.json', inputs)
    by_component = {record['component']: record for record in records}
    os_source = Path(by_component['OS']['path'])
    decoded = decode_os_raw(os_source, output / 'OS-20G81.raw.cdr', evidence)
    mount_root = output.parent / 'mounts'
    inspections = {}
    for tag, image in (('OS', decoded['raw_path']),
                       ('SystemOS', by_component['Cryptex1,SystemOS']['path']),
                       ('AppOS', by_component['Cryptex1,AppOS']['path'])):
        inspections[tag] = inspect_apfs(image, tag, evidence, mount_root)
    version = inspections['OS'].get('system_version') or {}
    if (version.get('ProductVersion'), version.get('ProductBuildVersion')) != ('16.6.1', '20G81'):
        raise AssetError('mounted OS version/build differs from restore identity')
    data_path = output.parent / 'host-data/Data-host-only-20G81-case-sensitive.dmg'
    if not data_path.exists():
        data_path.parent.mkdir(parents=True, exist_ok=True)
        command = ['/usr/bin/hdiutil', 'create', '-size', '2048m', '-fs', 'Case-sensitive APFS',
                   '-volname', 'Data', '-layout', 'NONE', '-type', 'UDIF', str(data_path)]
        result = subprocess.run(command, capture_output=True, timeout=180)
        write_json(evidence / 'Data-create-from-helper.json', dict(command=command,
                   returncode=result.returncode, stdout=result.stdout.decode(errors='replace'),
                   stderr=result.stderr.decode(errors='replace'), phone_actions=0))
        if result.returncode:
            raise AssetError('host Data template creation failed')
    inspections['Data'] = inspect_apfs(data_path, 'Data', evidence, mount_root)
    report = dict(schema='thor-fullseq01-prepared-system-inputs-v1', created_at=utc(),
                  result='PASS_HOST_ASSETS_PREPARED_THOR_RUNTIME_ROOT_BLOCKED',
                  producer_path=str(Path(__file__).resolve()), producer_sha256=hash_file(__file__),
                  identity=IDENTITY, fetched_report=str(fetch_path), fetched_report_sha256=hash_file(fetch_path),
                  loader_inputs=inputs, os_raw=decoded, host_inspections=inspections,
                  root_volume_uuid=inspections['OS']['volume']['VolumeUUID'],
                  systemos_volume_uuid=inspections['SystemOS']['volume']['VolumeUUID'],
                  appos_volume_uuid=inspections['AppOS']['volume']['VolumeUUID'],
                  data_volume_uuid=inspections['Data']['volume']['VolumeUUID'],
                  data_template_empty=True, data_ios16_compatibility_qualified=False,
                  official_images_readable_on_host=True, thor_root_medium=None,
                  thor_root_media_qualified=False, runtime_mount_proven=False,
                  iphone_or_ipad_identity_is_physical_thor_identity=False,
                  boot_eligible=False, ios_home_screen_proven=False, phone_actions=0,
                  blocked=['Thor root backing medium / no phone storage installed',
                           'native APFS instruction relocation and Darwin22 ABI qualification',
                           'Thor native storage/VM/platform runtime',
                           'Data iOS16 feature compatibility and required initial content',
                           'native launchd, services and SpringBoard execution'])
    write_json(evidence / 'prepared-system-inputs.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--inspect-prepared', action='store_true')
    args = parser.parse_args()
    try:
        result = (inspect_prepared(args.root, args.evidence, args.output) if args.inspect_prepared else
                  prepare(args.root, args.evidence, args.output,
                          workers=args.workers, inventory_only=args.inventory_only))
    except Exception as exc:
        print(json.dumps(dict(result='BLOCKED_HOST_ASSET_PREPARATION',
                              error_type=type(exc).__name__, error=str(exc), phone_actions=0)), flush=True)
        raise
    print(json.dumps(dict(result=result.get('result', 'PASS_IDENTITY_INVENTORY'),
                          components=len(result.get('inputs', result.get('components', []))),
                          phone_actions=0)), flush=True)


if __name__ == '__main__':
    main()
