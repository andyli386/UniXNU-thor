"""Attribute the boot using the staging message emitted for the v227 diagnostic library."""
from pathlib import Path
import runpy
import uuid

base = runpy.run_path(str(Path(__file__).resolve().with_name("legacy_history.py")))
STAGE = b"THOR CONSOLE V205: staged /private/var/thor-console-v227/libpong-console-v205.dylib bytes="

def inventory(root, files):
    result = base["inventory"](root, files)
    paths = {r["path"]: r for r in files if r.get("sha256")}
    sources = {}
    for family in base["FAMILIES"]:
        matches = []
        for suffix in (".previous.txt", ".txt"):
            row = paths.get(f"logs/{family}{suffix}")
            if not row:
                continue
            boot = None
            for line in base["recovered"](root, row).splitlines():
                if line.startswith(b"Pong console capture BEGIN"):
                    marker = base["MARK"].fullmatch(line)
                    boot = str(uuid.UUID(marker[1].decode())).upper() if marker else None
                elif boot and line.startswith(b"[") and STAGE in line and boot not in matches:
                    matches.append(boot)
        sources[family] = matches
    first, second = sources.values()
    common = [b for b in first if b in second]
    if common != [b for b in second if b in first]:
        raise ValueError("The two console histories disagree on v227 boot order")
    return {"console_sessions": result["console_sessions"], "latest": result["latest"],
            "trace_only_candidates": result["trace_only_candidates"],
            "v227_by_console": sources, "corroborated_v227": common}
