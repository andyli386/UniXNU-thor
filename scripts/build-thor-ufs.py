#!/usr/bin/env python3
"""Build the thor UFS driver archive the kernel links when XNU_NATIVE_UFS=1.

The driver is device-owned staging code, so it is not in the component catalog;
this recipe compiles its sources with the exact command line XNU used for its
own platform expert and emits the archive path the kernel build expects
(build/sheng-ufs/libShengUfs.a).  Same contract as build-io-storage-family.py,
which produces the IOBlockStorageDevice base class the driver derives from.
"""
from pathlib import Path
import json, shlex, shutil, subprocess, sys

TREE = Path.cwd()
BUILD = TREE / "build/sheng-ufs"
DRIVER = TREE / "drivers/ThorUfs"
COMPILE_DB = TREE / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/compile_commands.json"
STORAGE_HEADERS = TREE / "build/storage-abi/include"
ARCHIVE = BUILD / "libShengUfs.a"
SOURCES = ["NativeUfs.cpp", "PongUfsTransport.cpp"]


def fail(message):
    raise SystemExit(message)


if not DRIVER.is_dir():
    fail(f"driver source missing: {DRIVER}")
if not COMPILE_DB.is_file():
    fail(f"XNU compile database missing: {COMPILE_DB}")
if not STORAGE_HEADERS.is_dir():
    fail("storage ABI headers missing; run prepare-storage-headers.py first")

entries = json.loads(COMPILE_DB.read_text())
entry = next((item for item in entries if Path(item.get("file", "")).name == "IOPlatformExpert.cpp"), None)
if entry is None:
    fail("no IOPlatformExpert.cpp command in the XNU compile database")

BUILD.mkdir(parents=True, exist_ok=True)
objects = []
for name in SOURCES:
    source = DRIVER / name
    if not source.is_file():
        fail(f"missing source: {source}")
    obj = BUILD / (Path(name).stem + ".obj")
    arguments = shlex.split(entry["command"])
    try:
        arguments[arguments.index("-o") + 1] = str(obj)
    except (ValueError, IndexError):
        fail("IOPlatformExpert compile command has no usable -o argument")
    arguments[-1] = str(source)
    arguments.extend(["-I", str(DRIVER), "-I", str(STORAGE_HEADERS),
                      "-I", str(TREE / "build/storage-abi/include/IOKit/storage")])
    print(f"compiling {name}")
    subprocess.run(arguments, cwd=entry["directory"], check=True)
    objects.append(str(obj))

ar = shutil.which("ar") or fail("ar is required to create libShengUfs.a")
subprocess.run([ar, "-rcs", str(ARCHIVE), *objects], check=True)
ranlib = shutil.which("ranlib")
if ranlib:
    subprocess.run([ranlib, str(ARCHIVE)], check=True)
print(f"built {ARCHIVE}")
