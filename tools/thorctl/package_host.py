"""Host-only packaging primitives. No device operations or raw entropy logging."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

TOOL_KEYS = ("mkbootimg", "unpack_bootimg", "image_verifier",
             "gki_certificate", "inspect_thor_fdt")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def rooted(root, path):
    value = (root / path).resolve()
    require(value.is_relative_to(root), "Path escapes workspace")
    return value


def seed_source(platform):
    if platform.startswith("linux"):
        return "linux-os.getrandom(flags=0)"
    if platform == "darwin":
        return "darwin-os.urandom"
    raise RuntimeError("Unsupported OS CSPRNG platform")


def private_seed(platform=None):
    """One blocking OS CSPRNG call; the caller must never log the bytes."""
    platform = sys.platform if platform is None else platform
    source = seed_source(platform)
    value = os.getrandom(64, 0) if platform.startswith("linux") else os.urandom(64)
    require(isinstance(value, bytes) and len(value) == 64 and any(value),
            "Invalid OS CSPRNG result")
    return value, source


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tools(root, manifest_path):
    """Pin interpreter and all non-stdlib script imports before entropy."""
    manifest = rooted(root, manifest_path)
    data = json.loads(manifest.read_text())
    require(data.get("schema") == "thor-package-tools-v1", "Tool manifest schema")
    python = Path(data["python"]["path"])
    python = (root / python).resolve()
    require(python.is_file() and sha(python) == data["python"]["sha256"],
            "Pinned Python identity")
    version_run = subprocess.run(
        [str(python), "-E", "-s", "-c",
         "import json,sys;print(json.dumps(list(sys.version_info[:3])))"],
        capture_output=True, text=True, check=True, timeout=15)
    version = json.loads(version_run.stdout)
    require(version == data["python"]["version"] and version[:2] >= [3, 11],
            "Pinned Python version")
    scripts = {}
    records = {}
    for key in TOOL_KEYS:
        entry = data["scripts"][key]
        path = rooted(root, Path(entry["path"]))
        require(path.is_file() and sha(path) == entry["sha256"],
                "Pinned packaging script: " + key)
        scripts[key] = path
        records[key] = {"path": str(path), "sha256": entry["sha256"]}
    require(scripts["gki_certificate"] ==
            scripts["mkbootimg"].parent / "gki/generate_gki_certificate.py",
            "Pinned GKI import must be beside mkbootimg")
    require(scripts["inspect_thor_fdt"] ==
            scripts["image_verifier"].parent / "inspect_thor_fdt.py",
            "Pinned FDT import must be beside image verifier")
    gki_init = scripts["gki_certificate"].parent / "__init__.py"
    require(not gki_init.exists(), "Unpinned GKI package initializer")
    metadata = {"manifest_sha256": sha(manifest),
                "python": {"path": str(python), "sha256": sha(python),
                           "version": version}, "scripts": records}
    return python, scripts, metadata


def package_commands(python, scripts, out):
    def run(key):
        return [str(python), "-E", "-s", str(scripts[key])]
    image = out / "thor-boot.img"
    return [
        run("mkbootimg") + [
            "--header_version", "4", "--kernel", str(out / "kernel.gz-dtb"),
            "--ramdisk", str(out / "kernel-bundle.bin"),
            "--os_version", "12.0.0", "--os_patch_level", "2024-09",
            "--cmdline", "", "-o", str(image)],
        run("image_verifier") + [
            str(image), "--expected-header-version", "4",
            "--kernel-format", "gzip-bootshim-fd-dtb",
            "--shim", str(out / "BootShim.bin"),
            "--fd", str(out / "SILICIUM_UEFI.fd"),
            "--dtb", str(out / "thor-stock-vendor-dtb.bin"),
            "--ramdisk", str(out / "kernel-bundle.bin"),
            "--fd-base", "0xA7100000", "--fd-size", "0x300000",
            "--max-image-size", "201326592",
            "--strict-padding", "--require-exact-size"],
        run("unpack_bootimg") + [
            "--boot_img", str(image), "--out", str(out / "unpacked")],
    ]


@contextmanager
def private_output(out, timestamp):
    """No overwrite/retry; every interrupted package stays quarantined."""
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        yield out
    except BaseException:
        with (out / "INVALID-NOT-FOR-PHONE").open("x") as stream:
            stream.write(timestamp + "\n")
        raise
