#!/usr/bin/env python3
"""Compile and ad-hoc sign the Thor v172 console payload."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "userland/checkpoints/thor-v172-console.c"
MANIFEST = REPO / "patches/xnu-ipados18/checkpoints/thor-v176/manifest.json"
LIBRARY = "libpong-console-v172.dylib"
HEADER = "thor_console_v172_payload.h"
LOAD_PATH = f"/private/var/pong/{LIBRARY}"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory for the signed library and C payload header")
    parser.add_argument("--expect-shipped", action="store_true", help="require byte-identical v176 outputs")
    options = parser.parse_args()
    hashes = json.loads(MANIFEST.read_text())["console_sha256"]
    if digest(SOURCE) != hashes["console-v172.c"]:
        raise SystemExit("Console source does not match this checkpoint.")
    output = options.output.resolve()
    with tempfile.TemporaryDirectory(prefix="thor-v172-console-") as directory:
        temp = Path(directory)
        library = temp / LIBRARY
        subprocess.run([
            "xcrun", "--sdk", "iphoneos", "clang", "-target", "arm64-apple-ios18.0",
            "-dynamiclib", "-Os", "-Wl,-no_fixup_chains", "-install_name", LOAD_PATH,
            str(SOURCE), "-o", str(library),
        ], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(library)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(library)], check=True)
        contents = library.read_bytes()
        header = temp / HEADER
        with header.open("w", encoding="ascii", newline="\n") as text:
            text.write(f'#define THOR_CONSOLE_V172_STAGE_PATH "{LOAD_PATH}"\n')
            text.write("static const unsigned char thor_console_v172_payload[] = {\n")
            for start in range(0, len(contents), 16):
                row = ", ".join(f"0x{byte:02x}" for byte in contents[start:start + 16])
                text.write(f"    {row},\n")
            text.write("};\n")
        for artifact in (library, header):
            value = digest(artifact)
            if options.expect_shipped and value != hashes[artifact.name]:
                raise SystemExit(f"Built {artifact.name} differs from shipped SHA-256: {value}")
            print(f"{artifact.name} sha256={value}")
        output.mkdir(parents=True, exist_ok=True)
        for artifact in (library, header):
            shutil.copyfile(artifact, output / artifact.name)


if __name__ == "__main__":
    main()
