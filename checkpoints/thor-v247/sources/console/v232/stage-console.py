#!/opt/homebrew/bin/python3.13
"""Embed the v232 console-capture build in the v232 kernel recipe and record it.

Mirrors what prepare/stage-collector.py did for v223: regenerate the payload header, point the two
kernel staging references at a new directory, and update runtime/final-inputs.json.
usage: stage-console.py stage|verify"""
import hashlib, json, re, subprocess, sys
from pathlib import Path
out = Path(__file__).resolve().parent.parent
root = out / "v232-unified"
base = out / "v231-unified"
work = out / "v232-intake" / "console"
name = "libpong-console-v205.dylib"
new_dir, new_path = "thor-console-v232", "/private/var/thor-console-v232/libpong-console-v205.dylib"
old_dir, old_path = "thor-console-v227", "/private/var/thor-console-v227/libpong-console-v205.dylib"
blob = (work / name).read_bytes()
sha = hashlib.sha256(blob).hexdigest()
subprocess.run(["codesign", "--verify", "--strict", str(work / name)], check=True)
assert subprocess.check_output(["xcrun", "lipo", "-archs", str(work / name)], text=True).split() == ["arm64"]
info = subprocess.check_output(["xcrun", "vtool", "-show-build", str(work / name)], text=True)
assert "platform IOS" in info and "minos 18.0" in info
subprocess.run(["xcrun", "dyld_info", "-validate_only", str(work / name)], check=True, stdout=subprocess.DEVNULL)
uuid = subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(work / name)], text=True).split()[1]
kern = root / "build-tree/.cache/xnu-work/bsd/kern"
header = kern / "thor_console_v172_payload.h"
source_path = kern / "kern_exec.c"
previous = json.loads((base / "runtime/final-inputs.json").read_bytes())
old = previous["payloads"][name]
assert previous["diagnostic_stage_path"] == old_path and old["sha256"] == hashlib.sha256((base / "runtime" / name).read_bytes()).hexdigest()
wanted = json.loads(json.dumps(previous))
wanted["payloads"][name] = dict(sha256=sha, uuid=uuid, bytes=len(blob))
wanted["diagnostic_stage_path"] = new_path
action = sys.argv[1] if len(sys.argv) > 1 else "verify"
lines = [f'#define THOR_CONSOLE_V172_STAGE_PATH "{new_path}"', "static const unsigned char thor_console_v172_payload[] = {"]
for off in range(0, len(blob), 16):
    lines.append("  " + "".join(f"0x{x:02x}," for x in blob[off:off + 16]) + ("" if off + 16 < len(blob) else ""))
lines.append("};")
text = "\n".join(lines) + "\n"
if action == "stage":
    src = source_path.read_text()
    assert src.count(old_dir) == 2 and src.count(new_dir) == 0
    if (base / kern.relative_to(root) / "kern_exec.c").read_text() != src:
        # first stage from the clone; the clone's copy must equal its parent at this point
        assert "thor-console-v232" not in src
    source_path.write_text(src.replace(old_dir, new_dir))
    header.write_text(text)
    (root / "runtime" / name).write_bytes(blob)
    (root / "runtime" / "final-inputs.json").write_text(json.dumps(wanted, indent=2) + "\n")
src = source_path.read_text()
assert src.count(new_dir) == 2 and old_dir not in src
assert f'thor_stage_ensure_directory(data, "{new_dir}"' in src
assert f'thor_stage_data_payload(p, "/private/var/{new_dir}", THOR_CONSOLE_V172_STAGE_PATH' in src
assert "static const char library[] = THOR_CONSOLE_V172_STAGE_PATH;" in src
got = header.read_text()
assert got.startswith(f'#define THOR_CONSOLE_V172_STAGE_PATH "{new_path}"\n') and got.endswith("};\n")
body = got.split("\n", 2)[2].rsplit("};", 1)[0]
assert bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", body)) == blob
assert (root / "runtime" / name).read_bytes() == blob
assert json.loads((root / "runtime/final-inputs.json").read_bytes()) == wanted
assert json.loads((root / "runtime/inputs.json").read_bytes()) == json.loads((base / "runtime/inputs.json").read_bytes())
print(json.dumps(dict(name=name, sha256=sha, uuid=uuid, bytes=len(blob), stage_path=new_path, source=str(source_path)), indent=1))
