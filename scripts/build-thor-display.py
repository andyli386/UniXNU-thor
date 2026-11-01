#!/usr/bin/env python3
"""Build the GOP framebuffer archive the kernel links when MobileDisplay is on.

NativeMobileDisplay/NativeLegacyDisplay supply IOMobileFramebuffer themselves
(OriginalMobileFramebufferABI.h declares the whole vtable locally), so no
prelinked bindings are involved: the sources compile against the kernel headers
with the same command line XNU used for its platform expert.

Output contract copied from the upstream flow the kernel script expects:
build/gop-framebuffer/libGopFramebuffer.late.a plus build-report.json whose
archive_sha256 the kernel build re-checks before force-loading it.
"""
from pathlib import Path
import hashlib, json, shutil, shlex, subprocess, sys

TREE = Path.cwd()
BUILD = TREE / "build/gop-framebuffer"
DRIVER = TREE / "drivers/GopFramebuffer"
COMPILE_DB = TREE / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/compile_commands.json"
STORAGE_HEADERS = TREE / "build/storage-abi/include"
ARCHIVE = BUILD / "libGopFramebuffer.late.a"
EARLY = BUILD / "libGopFramebuffer.a"
# NativeLegacyDisplay.cpp needs OriginalLegacyFramebufferABI.h, which this
# checkout does not carry; the mobile framebuffer is the one iOS actually opens.
SOURCES = ["NativeMobileDisplay.cpp"]


def fail(message):
    raise SystemExit(message)


if not DRIVER.is_dir():
    fail(f"driver source missing: {DRIVER}")
if not COMPILE_DB.is_file():
    fail(f"XNU compile database missing: {COMPILE_DB}")

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
    arguments.extend(["-I", str(DRIVER), "-I", str(STORAGE_HEADERS)])
    print(f"compiling {name}")
    subprocess.run(arguments, cwd=entry["directory"], check=True)
    objects.append(str(obj))

ar = shutil.which("ar") or fail("ar is required")
subprocess.run([ar, "-rcs", str(ARCHIVE), *objects], check=True)
ranlib = shutil.which("ranlib")
if ranlib:
    subprocess.run([ranlib, str(ARCHIVE)], check=True)
# The early archive is not linked by this board yet; keep the path present so a
# later switch to the early stage does not silently pick up a stale file.
shutil.copyfile(ARCHIVE, EARLY)

digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
report = {
    "archive": str(ARCHIVE),
    "archive_sha256": digest,
    "sources": [str((DRIVER / name).relative_to(TREE)) for name in SOURCES],
    "source_sha256": {str((DRIVER / name).relative_to(TREE)):
                      hashlib.sha256((DRIVER / name).read_bytes()).hexdigest() for name in SOURCES},
    "compiler": entry["command"].split()[0],
}
(BUILD / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"built {ARCHIVE} sha256 {digest}")
