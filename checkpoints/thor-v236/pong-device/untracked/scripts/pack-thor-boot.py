#!/usr/bin/env python3
"""Package a Thor development APFS boot image from the known-good Android layout."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import uuid
import zlib

from pong_adt_contract import parse_adt
from pong_boot_command_line import require_fits


PAGE = 4096
FD_SIZE = 3 * 1024**2
SHIM_SIZE = 112
FAT_SIZE = 160 * 1024**2
EFI = '::/EFI/UniXNU/'
ADT = EFI + 'Payloads/kernel.macho.adt'
KERNEL = EFI + 'Payloads/kernel.macho'
TOML = EFI + 'unixnu.toml'


def padded(length):
    return (length + PAGE - 1) & -PAGE


def copy_bytes(source, destination, length):
    while length:
        chunk = source.read(min(length, 1024**2))
        if not chunk:
            raise ValueError('Truncated boot image')
        destination.write(chunk)
        length -= len(chunk)


def read_layout(image):
    with image.open('rb') as stream:
        header = bytearray(stream.read(PAGE))
        if len(header) != PAGE or header[:8] != b'ANDROID!' or struct.unpack_from('<I', header, 40)[0] != 4:
            raise ValueError('Expected an Android v4 boot image')
        kernel_size, fat_size = struct.unpack_from('<II', header, 8)
        signature_size = struct.unpack_from('<I', header, 1580)[0]
        fat_offset = PAGE + padded(kernel_size)
        if not kernel_size or fat_size != FAT_SIZE or signature_size not in (0, PAGE):
            raise ValueError('Unexpected Thor boot image sizes')
        kernel = stream.read(kernel_size)
        stream.seek(fat_offset + fat_size)
        tail = stream.read()
        if len(tail) != signature_size or any(tail):
            raise ValueError('Template must have an empty signature region')
    return header, kernel, fat_offset, tail


def pack_firmware(kernel, firmware):
    if firmware is None:
        return kernel
    decoder = zlib.decompressobj(wbits=31)
    prior = decoder.decompress(kernel, SHIM_SIZE + FD_SIZE + 1)
    vendor_fdt = decoder.unused_data
    if (not decoder.eof or len(prior) != SHIM_SIZE + FD_SIZE
            or not vendor_fdt.startswith(bytes.fromhex('d00dfeed'))
            or struct.unpack_from('>I', vendor_fdt, 4)[0] != len(vendor_fdt)):
        raise ValueError('Template lacks the Thor shim, FD and vendor FDT layout')
    fd = firmware.read_bytes()
    if len(fd) != FD_SIZE:
        raise ValueError('Expected a 3 MiB Thor FD')
    return gzip.compress(prior[:SHIM_SIZE] + fd, mtime=0) + vendor_fdt


def read_member(image, name):
    return subprocess.check_output(['mcopy', '-i', image, name, '-'])


def boot_config(template, boot_args, omitted, timeout, menu_title=None):
    config = template.decode('utf-8')
    entry, = (item for item in tomllib.loads(config)['entry'] if item['type'] == 'xnu')
    if entry['path'] != 'Payloads/kernel.macho':
        raise ValueError('Template XNU entry has an unexpected path')
    words = [word for word in entry['args'].split() if word.split('=', 1)[0] not in omitted]
    for word in boot_args:
        if not word or not word.isascii() or any(char.isspace() for char in word):
            raise ValueError(f'Invalid kernel boot argument: {word!r}')
        key = word.split('=', 1)[0]
        words = [previous for previous in words if previous.split('=', 1)[0] != key]
        words.append(word)
    command = ' '.join(words)
    require_fits(command)
    config, count = re.subn(r'^args\s*=.*$', lambda _: 'args = ' + json.dumps(command), config, flags=re.M)
    if count != 1:
        raise ValueError('Template must have a single row of XNU arguments')
    config, count = re.subn(r'^(\[boot\]\n)timeout\s*=.*$', lambda m: m[1] + f'timeout = {timeout}', config, flags=re.M)
    if count != 1:
        raise ValueError('Template must have an explicit boot menu timeout')
    if menu_title is not None:
        config, count = re.subn(r'^(\[ui\]\n)title\s*=.*$',
                                lambda m: m[1] + 'title = ' + json.dumps(menu_title), config, flags=re.M)
        if count != 1:
            raise ValueError('Template must have an explicit boot menu title')
    parsed = tomllib.loads(config)
    if parsed['boot']['timeout'] != timeout or not any(e.get('args') == command for e in parsed['entry']):
        raise ValueError('Encoded boot configuration differs from requested settings')
    if menu_title is not None and parsed['ui']['title'] != menu_title:
        raise ValueError('Encoded boot menu title differs from requested title')
    return config.encode('utf-8'), command


def validate_root(adt, root_uuid, manifest):
    nodes = parse_adt(adt)
    chosen = nodes['/chosen']
    matcher = chosen.get('root-matching', b'')
    root = {'IOProviderClass': 'IOMedia', 'IOPropertyMatch': {'UUID': root_uuid}}
    if not matcher.endswith(b'\0') or plistlib.loads(b'<plist>' + matcher[:-1] + b'</plist>') != root:
        raise ValueError('ADT must select the installed APFS system volume')
    if ('/arm-io/ufshc' not in nodes or chosen.get('debug-enabled') != struct.pack('<I', 1)
            or chosen.get('boot-manifest-hash') != hashlib.sha384(manifest).digest()):
        raise ValueError('ADT is missing the Thor UFS or development manifest policy')
    if chosen.get('darwinboot-mem-diag') != bytes(4096):
        raise ValueError('ADT is missing its EFI map diagnostic buffer')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('template', 'kernel', 'volume-layout', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--firmware', type=Path)
    parser.add_argument('--boot-arg', action='append', default=[])
    parser.add_argument('--omit-boot-arg', action='append', default=[], metavar='KEY')
    parser.add_argument('--menu-title')
    parser.add_argument('--software-platform-identity', action='store_true',
                        help='derive stable Thor identity seeds from pong_platform_uuid and use UDID version 2')
    parser.add_argument('--menu-timeout', type=int, default=2, choices=range(1, 11))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    layout = json.loads(args.volume_layout.read_text())
    root_uuid = str(uuid.UUID(layout['os']['uuid'])).upper()
    header, kernel, fat_offset, tail = read_layout(args.template)
    kernel = pack_firmware(kernel, args.firmware)
    struct.pack_into('<I', header, 8, len(kernel))

    with tempfile.TemporaryDirectory(prefix='thor-pack-', dir=output.parent) as temporary:
        stage = Path(temporary)
        fat = stage / 'payload.fat'
        with args.template.open('rb') as template, fat.open('wb') as destination:
            template.seek(fat_offset)
            copy_bytes(template, destination, FAT_SIZE)
        config, command = boot_config(read_member(fat, TOML), args.boot_arg,
                                      args.omit_boot_arg, args.menu_timeout, args.menu_title)
        config_path = stage / 'unixnu.toml'
        config_path.write_bytes(config)
        manifest = json.dumps({
            'apple_personalization': False,
            'board': 'Xiaomi 12S Ultra / Thor / SM8475',
            'boot_args': command,
            'development_debug': True,
            'format': 'pong-development-boot-manifest-v1',
            'root_volume_uuid': root_uuid,
        }, sort_keys=True, separators=(',', ':')).encode() + b'\n'
        manifest_path = stage / 'pong-boot-manifest.json'
        manifest_path.write_bytes(manifest)
        adt_path = stage / 'kernel.macho.adt'
        generator = Path(__file__).with_name('make-thor-adt-ipados18.py')
        adt_args = [sys.executable, generator, '--cpus', '8', '--dram-size', '0x300000000',
                    '--ufs', '--volume-layout', args.volume_layout, '--development-debug',
                    '--boot-manifest', manifest_path, '--skip-image-verification', '--output', adt_path]
        if args.software_platform_identity:
            values = [word.partition('=')[2] for word in command.split()
                      if word.startswith('pong_platform_uuid=')]
            if len(values) != 1 or uuid.UUID(values[0]).int == 0:
                raise ValueError('Thor software identity requires one nonzero pong_platform_uuid')
            # Stable software namespace. Do not copy an Apple hardware identifier.
            digest = hashlib.sha256(b'thor-software-ecid\0' + uuid.UUID(values[0]).bytes).digest()
            software_ecid = int.from_bytes(digest[:8], 'little') or 1
            software_chip_id = 0x8475
            adt_args += ['--unique-chip-id', hex(software_ecid), '--chip-id', hex(software_chip_id),
                         '--udid-version', '2']
        subprocess.run(adt_args, check=True)
        for source, target in ((args.kernel, KERNEL), (config_path, TOML), (adt_path, ADT),
                               (manifest_path, EFI + 'pong-boot-manifest.json')):
            subprocess.run(['mcopy', '-i', fat, '-o', source, target], check=True)
        packaged_adt = read_member(fat, ADT)
        validate_root(packaged_adt, root_uuid, manifest)
        if args.software_platform_identity:
            nodes = parse_adt(packaged_adt)
            if (nodes['/chosen']['unique-chip-id'] != struct.pack('<Q', software_ecid)
                    or nodes['/chosen']['chip-id'] != struct.pack('<I', software_chip_id)
                    or nodes['/product']['udid-version'] != struct.pack('<I', 2)):
                raise ValueError('Thor software identity did not survive FAT packaging')
        candidate = stage / 'candidate.img'
        with candidate.open('wb') as file, fat.open('rb') as payload:
            file.write(header)
            file.write(kernel)
            file.write(bytes(padded(len(kernel)) - len(kernel)))
            shutil.copyfileobj(payload, file)
            file.write(tail)
        os.link(candidate, output)
        print(f'{output} | APFS root {root_uuid} | {len(command)} boot argument bytes')


if __name__ == '__main__':
    main()
