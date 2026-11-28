"""The format of the Thor boot image, without any older packaging script.

An image is an Android v4 boot image whose ramdisk section is a FAT16 volume holding an EFI tree:

    /efi/unixnu/payloads/kernel.macho           the prelinked kernel
    /efi/unixnu/pong-boot-manifest.json         what this image is (version, digests, boot arguments, changes)
    /efi/unixnu/unixnu.toml                     the loader's menu (title and the XNU entry's command line)

A version is packaged from a full template image (inputs/image-template) by replacing those members with mtools
under a fixed timestamp and trimming the ramdisk to its used clusters (compact).  Everything here reads the image
independently of mtools.  The functions are the ones the packaging scripts of v152 to v247 used, collected here.
"""
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

PAGE, SECTOR = 4096, 512
KERNEL_MEMBER = "/efi/unixnu/payloads/kernel.macho"
MANIFEST_MEMBER = "/efi/unixnu/pong-boot-manifest.json"
MENU_MEMBER = "/efi/unixnu/unixnu.toml"
TEMPLATE = "boot-thor-unixnu-v202-b-software.img"
TEMPLATE_SHA256 = "68d90a081441d5a504d8583f09fbd29e1e94560cc602408d97d6199a618a056b"
PACK_EPOCH = 1795428802                 # every member of every image carries this timestamp
BOOT_STACK = 0x4000                     # INTSTACK_SIZE (4 * 4096): the stack arm_init() and kernel_bootstrap() run on


def check(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def json_bytes(data):
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


# ---------------------------------------------------------------------------------------------- reading
U16 = lambda b, o: struct.unpack_from("<H", b, o)[0]
U32 = lambda b, o: struct.unpack_from("<I", b, o)[0]


def decode(path):
    """(members by lower-case path, firmware digest, signature digest, image digest) of an Android v4 boot image"""
    raw = Path(path).read_bytes()
    assert raw[:8] == b"ANDROID!" and U32(raw, 40) == 4
    start = 4096 + ((U32(raw, 8) + 4095) // 4096) * 4096
    size = U32(raw, 12)
    body = memoryview(raw)[start:start + size]
    assert start + size + U32(raw, 1580) == len(raw)
    sec = U16(body, 11)
    cluster = int(body[13]) * sec
    table_start = U16(body, 14) * sec
    table_size = U16(body, 22) * sec
    copies = body[16]
    assert sec == 512 and copies == 2
    table = body[table_start:table_start + table_size]
    assert table == body[table_start + table_size:table_start + 2 * table_size]
    roots = table_start + copies * table_size
    roots_size = ((U16(body, 17) * 32 + sec - 1) // sec) * sec
    first = roots + roots_size
    members = {}
    used = set()

    def content(index):
        chunks = []
        while index < 0xfff8:
            assert 2 <= index < 0xfff0 and index not in used
            used.add(index)
            off = first + (index - 2) * cluster
            assert off + cluster <= len(body)
            chunks.append(body[off:off + cluster])
            index = U16(table, index * 2)
        return b"".join(chunks)

    def directory(name, entries):
        long = {}
        for ofs in range(0, len(entries), 32):
            entry = entries[ofs:ofs + 32]
            if not entry[0]:
                break
            if entry[0] == 0xe5:
                long = {}
                continue
            attr = entry[11]
            if attr == 0xf:
                order = entry[0] & 0x3f
                if entry[0] & 0x40:
                    long = {}
                long[order] = bytes(entry[1:11]) + bytes(entry[14:26]) + bytes(entry[28:32])
                continue
            base = bytes(entry[:8]).decode("cp437").rstrip()
            ext = bytes(entry[8:11]).decode("cp437").rstrip()
            short = base + ("." + ext if ext else "")
            leaf = (b"".join(long[i] for i in range(1, len(long) + 1)).decode("utf-16-le").split("\0", 1)[0] if long else short)
            long = {}
            if attr & 8 or leaf in (".", ".."):
                continue
            key = name + "/" + leaf.casefold()
            cid = U16(entry, 26)
            data = content(cid) if cid else b""
            if attr & 0x10:
                directory(key, data)
            else:
                size = U32(entry, 28)
                assert size <= len(data) and key not in members
                members[key] = data[:size]

    directory("", body[roots:roots + roots_size])
    front = bytearray(raw[:start])
    front[12:16] = bytes(4)
    return members, sha(front), sha(raw[start + size:]), sha(raw)


def sections(image):
    """(boot header and what precedes the ramdisk, ramdisk offset, ramdisk size, vbmeta signature) of an image"""
    with Path(image).open("rb") as fp:
        header = fp.read(PAGE)
        check(header[:8] == b"ANDROID!" and struct.unpack_from("<I", header, 40)[0] == 4, "v4 boot header")
        firmware, ramdisk = struct.unpack_from("<II", header, 8)
        offset = PAGE + (firmware + PAGE - 1) // PAGE * PAGE
        front = bytearray(header + fp.read(offset - PAGE))
        check(len(front) == offset and ramdisk % PAGE == 0, "v4 section alignment")
        fp.seek(offset + ramdisk)
        signature = fp.read()
        check(len(signature) == struct.unpack_from("<I", header, 1580)[0] == PAGE, "v4 signature length")
        return front, offset, ramdisk, signature


def volume(image):
    """the mtools name of the FAT volume inside an image"""
    return f"{image}@@{sections(image)[1]}"


def compact(full, target):
    """trim the FAT volume of a full image to the clusters it uses and write the result as a new image"""
    prefix, offset, length, signature = sections(full)
    with Path(full).open("rb") as src:
        src.seek(offset)
        body = bytearray(src.read(length))
    spc, nfat = body[13], body[16]
    bps, = struct.unpack_from("<H", body, 11)
    reserved, = struct.unpack_from("<H", body, 14)
    root_entries, = struct.unpack_from("<H", body, 17)
    fat_sectors, = struct.unpack_from("<H", body, 22)
    check(bps == SECTOR and nfat == 2 and body[54:62] == b"FAT16   ", "FAT16")
    first = reserved + nfat * fat_sectors + (root_entries * 32 + SECTOR - 1) // SECTOR
    tb = reserved * SECTOR
    size = fat_sectors * SECTOR
    entries = [v for (v,) in struct.iter_unpack("<H", body[tb:tb + size])]
    check(body[tb:tb + size] == body[tb + size:tb + 2 * size], "different FAT copies")
    count = (length // SECTOR - first) // spc
    check(all(v == 0 for v in entries[count + 2:]), "garbage beyond FAT extent")
    highest = max(i for i in range(2, count + 2) if entries[i])
    check(not any(v and not (2 <= v <= highest or 0xfff8 <= v <= 0xffff) for v in entries[2:highest + 1]), "FAT cluster link")
    end = (first + (highest - 1) * spc) * SECTOR
    trimmed = (end + PAGE - 1) // PAGE * PAGE
    check(4085 <= (trimmed // SECTOR - first) // spc < 65525, "not FAT16 after trim")
    struct.pack_into("<I", prefix, 12, trimmed)
    struct.pack_into("<I", body, 32, trimmed // SECTOR)
    with Path(target).open("xb") as f:
        f.write(prefix)
        f.write(memoryview(body)[:trimmed])
        f.write(signature)
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------------------------- menu
def new_menu(raw, cfg):
    """(menu bytes, command line) after changing the [ui] title and the XNU entry's arguments"""
    parent = tomllib.loads(raw.decode())
    section = re.compile(rb'(?ms)^(\[ui\]\s*\n)(.*?)(?=^\[|\Z)')

    def change(m):
        body, n = re.subn(rb'(?m)^title\s*=.*$', b'title = ' + json.dumps(cfg['title']).encode(), m[2])
        check(n == 1, 'missing UI title')
        return m[1] + body

    menu, n = section.subn(change, raw)
    check(n == 1, 'unexpected UI sections')
    parent['ui']['title'] = cfg['title']
    entry, = [e for e in parent['entry'] if e.get('type') == 'xnu']
    old = entry['args'].split()
    for src, dest in cfg['replace_args'].items():
        check(old.count(src) == 1 and dest not in old, 'expected unique menu token ' + src)
        old[old.index(src)] = dest
    for token in cfg.get('remove_args', []):
        check(old.count(token) == 1, 'missing or repeated argument ' + token)
        old.remove(token)
    for new in cfg.get('append_args', []):
        check(new not in old and new, 'ambiguous extra argument')
        old.append(new)
    args = ' '.join(old)
    check(menu.count(entry['args'].encode()) == 1, 'non-unique menu command line')
    menu = menu.replace(entry['args'].encode(), args.encode())
    entry['args'] = args
    check(tomllib.loads(menu.decode()) == parent, 'extra menu difference')
    return menu, args


def _device_script(name):
    """import one of the device repository's own scripts"""
    pong = Path(os.environ.get('THOR_PONG') or Path(__file__).resolve().parents[2] / 'UniXNU-thor')
    if str(pong / 'scripts') not in sys.path:
        sys.path.insert(0, str(pong / 'scripts'))
    return importlib.import_module(name)


def verify_abi():
    """the loader's and XNU's command-line buffers are both 1024 bytes: no menu line may be longer"""
    from pong_boot_command_line import LIMIT
    headers = ((Path(__file__).resolve().parent / 'abi/BootArgs.h', 'DARWIN_BOOT_LINE_LENGTH'),
               (Path(os.environ['THOR_PONG']) / 'refer/ipados18/xnu-11417.140.69/pexpert/pexpert/arm64/boot.h', 'BOOT_LINE_LENGTH'))
    for header, macro in headers:
        source = header.read_text()
        value = re.search(rf'^#define\s+{macro}\s+(\d+)\s*$', source, re.MULTILINE)
        if not value or int(value[1]) != LIMIT + 1 or f'CommandLine[{macro}]' not in source:
            raise ValueError(f'{header}: command-line ABI disagrees with {LIMIT + 1}-byte guard')


def validate_menu(menu, manifest):
    """every enabled XNU entry of the menu, with each confirmation choice, fits the command line"""
    from pong_boot_command_line import require_fits
    entries = [entry for entry in menu.get('entry', [])
               if entry.get('enabled', True) and entry.get('type') == 'xnu']
    if not entries:
        raise ValueError('no enabled XNU entries in final FAT menu')
    default = menu.get('boot', {}).get('default')
    rows = []
    for index, entry in enumerate(entries):
        name = entry.get('id', f'XNU entry {index}')
        base = entry.get('args', '')
        if not isinstance(base, str):
            raise ValueError(f'{name}: args must be a string')
        variants = [('base', base)]
        if 'confirm_args' in entry or 'confirm_titles' in entry:
            titles, extras = entry.get('confirm_titles'), entry.get('confirm_args')
            if not (isinstance(entry.get('confirm'), str) and isinstance(titles, list)
                    and isinstance(extras, list) and len(titles) == len(extras) and titles
                    and all(isinstance(s, str) for s in titles + extras)):
                raise ValueError(f'{name}: invalid confirmation choices')
            # Main.c appends one literal space, even if either side is empty.
            variants.extend((title, base + ' ' + extra) for title, extra in zip(titles, extras))
        for variant, command in variants:
            try:
                count = require_fits(command)
            except ValueError as err:
                raise ValueError(f'{name} [{variant}]: {err}') from err
            rows.append({'entry': name, 'choice': variant, 'argument_bytes': count})
    defaults = [entry for entry in entries if entry.get('id') == default]
    manifest_entries = defaults or entries
    if manifest.get('boot_args') not in [entry.get('args', '') for entry in manifest_entries]:
        raise ValueError('FAT manifest boot_args disagrees with XNU menu')
    return rows


# ---------------------------------------------------------------------------------------------- Mach-O
def segments(data):
    """(address, size in the file, file offset) of every segment that carries data"""
    ncmds = struct.unpack_from("<I", data, 16)[0]
    out, off = [], 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19:
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
            out.append((vmaddr, filesize, fileoff))
        off += size
    return out


def bytes_at(data, vmaddr, count):
    for va, filesize, fileoff in segments(data):
        if va <= vmaddr and vmaddr + count <= va + filesize:
            return data[fileoff + vmaddr - va:fileoff + vmaddr - va + count]
    raise ValueError("0x%x is not file-backed" % vmaddr)


def symbol_address(path, name):
    out = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", str(path)], text=True)
    hits = [int(l.split()[0], 16) for l in out.splitlines() if l.split()[-1:] == [name]]
    assert len(hits) == 1, (name, hits)
    return hits[0]


def prologue_frame(code):
    """Stack bytes a function's prologue reserves: pre-indexed 64-bit stp on sp plus sub sp, sp, #imm."""
    total = 0
    for (w,) in struct.iter_unpack("<I", code):
        if w & 0xFFE003E0 == 0xA9A003E0:            # stp xt, xt2, [sp, #imm7]!  (pre-index, 64-bit)
            imm7 = (w >> 15) & 0x7F
            total += (0x80 - imm7 if imm7 & 0x40 else -imm7) * 8
        elif w & 0xFF8003FF == 0xD10003FF:          # sub sp, sp, #imm{,lsl #12}
            imm12, shift = (w >> 10) & 0xFFF, (w >> 22) & 1
            total += imm12 << (12 if shift else 0)
        elif w & 0xFFFFFC1F == 0xD50323BF:          # mov sp, ...
            break
        if w == 0xD65F03C0:                         # ret
            break
    return total


# ---------------------------------------------------------------------------------------------- device tree
def parse_adt(blob):
    return _device_script('pong_adt_contract').parse_adt(blob)


def emit(nodes, path):
    """serialize a device tree node with its children, in the flat device-tree format"""
    entries = nodes[path]
    children = [key for key in nodes if key != "/" and key.rsplit("/", 1)[0] == path.rstrip("/")]
    result = bytearray(struct.pack("<II", len(entries), len(children)))
    for key, value in entries.items():
        result += struct.pack("<32sI", key.encode("ascii"), len(value))
        result += value
        result += bytes(-len(value) % 4)
    for child in children:
        result += emit(nodes, child)
    return result
