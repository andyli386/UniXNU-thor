import argparse
import hashlib
import json
from pathlib import Path
import subprocess

STAGING = Path(__file__).resolve().parent
PROJECT = STAGING.parents[1]
OUT = PROJECT / "thor-out"
REFERENCE = OUT / "v170-session-progress/runtime/libpong-ui.dylib"
RECIPE = OUT / "v158-metal-staging/ui/compile-switcher-runtime.json"
REFERENCE_DIGEST = "a3dc47b69b433eb65d3cf831f55cca602397f4bec4fcf3df5337e6f2115122bf"
CONSOLE_SOURCE = PROJECT / "UniXNU-pong/userland/boot_snapshot18.c"
MISSING_ASSETS = (
    OUT / "v119-display-route/runtime/touch.c",
    OUT / "v134-native-cadence/runtime/settings_watch_stack18.c",
    OUT / "v98-native-gpu/runtime/metal-smoke18.o",
)


def digest(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def sign_and_verify(binary):
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--pagesize", "4096", binary], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", binary], check=True)


def build(recipe, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    command = recipe.copy()
    command[command.index("-o") + 1] = str(output)
    subprocess.run(command, check=True)
    sign_and_verify(output)
    (output.parent / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Reproduce the signed v170 UI payload before rebuilding the console worker.")
    parser.add_argument("--assets-json", type=Path, help="Map the archived paths in the v158 recipe to recovered absolute paths.")
    args = parser.parse_args()
    substitutions = json.loads(args.assets_json.read_text()) if args.assets_json else {}
    if set(substitutions) - {str(asset) for asset in MISSING_ASSETS}:
        raise SystemExit("Only missing archived inputs in the v158 recipe can be substituted.")
    command = json.loads(RECIPE.read_text())
    for key, value in substitutions.items():
        if not Path(value).is_absolute() or not Path(value).is_file():
            raise SystemExit(f"Invalid recovered asset for {key}: {value}")
        command[command.index(key)] = value
    missing = [item for item in command[:command.index("-o")] if item.endswith((".c", ".o")) and not Path(item).is_file()]
    if missing:
        raise SystemExit("Missing v158 compiler inputs:\n" + "\n".join(missing))
    record = json.loads((STAGING / "source-sha256.json").read_text())
    console = STAGING / record["file"]
    if (digest(REFERENCE) != REFERENCE_DIGEST or digest(CONSOLE_SOURCE) != record["baseline_sha256"]
            or digest(console) != record["after_sha256"]):
        raise SystemExit("Hash mismatch for v170 payload or staged console source.")

    baseline = STAGING / "baseline/libpong-ui.dylib"
    build(command, baseline)
    if digest(baseline) != REFERENCE_DIGEST:
        raise SystemExit("Rebuilt baseline did not match the signed v170 UI payload. Candidate omitted.")
    print("Verified byte-identical signed v170 baseline.", flush=True)

    output = STAGING / "candidate/libpong-ui.dylib"
    command[command.index(str(CONSOLE_SOURCE))] = str(console)
    build(command, output)
    if b"Pong console capture POLICY poll_ms=1000" not in output.read_bytes():
        raise SystemExit("Patched console receipt is absent from the signed payload.")
    new_digest = digest(output)
    if new_digest == REFERENCE_DIGEST:
        raise SystemExit("The console runtime candidate matches v170.")
    manifest = {
        "baseline_ui_sha256": REFERENCE_DIGEST,
        "ui_sha256": new_digest,
        "inputs": {original: {"path": path, "sha256": digest(Path(path))}
                   for original, path in substitutions.items()},
        "console_source": record,
    }
    (output.parent / "verified-product.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified signed Thor console candidate: {output}", flush=True)


if __name__ == "__main__":
    main()
