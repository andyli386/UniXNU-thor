#!/usr/bin/env python3
"""Build only the thor ENTRY10 bounded VM-preparation UEFI configuration; no phone commands."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, re, shutil, subprocess
p = argparse.ArgumentParser()
p.add_argument("--root",type=Path,default=Path("/home/vincent/Projects/thor-unixnu"))
p.add_argument("--source",type=Path,required=True)
p.add_argument("--run-id",required=True)
p.add_argument("--build-id",required=True)
p.add_argument("--dry-run",action="store_true")
a = p.parse_args()
if not re.fullmatch(r"[a-z0-9-]+", a.run_id):
    p.error("run-id must contain lowercase letters, digits and hyphens")
r=a.root.resolve()
w=a.source.resolve()
if not (w.is_relative_to(r/'sources') or w.is_relative_to(r/'repos')):
    p.error("UEFI source must belong to the project source or repos directory")
if not (w/'Platforms/Xiaomi/thorPkg/thor.dsc').is_file():
    p.error("source is not a thor UEFI tree")
if not re.fullmatch(r"[a-z0-9-]+",a.build_id):
    p.error("invalid firmware build ID")
l=r/'runs'/a.run_id/'l2'/'uefi-build'
if l.exists():
    p.error("run output already exists: refusing overwrite")
e = os.environ.copy()
v = r / "toolchains/uefi-python/bin"
base = w / "Mu_Basecore/BaseTools"
e.update(WORKSPACE=str(w), EDK_TOOLS_PATH=str(base), PYTHON_COMMAND=str(v / "python"),
         PYTHONPATH=str(base / "Source/Python"), CLANG_BIN="/usr/bin/", CLANG_HOST_BIN="/usr/bin/",
         LANG="C", LC_ALL="C")
e["PACKAGES_PATH"] = os.pathsep.join(str(w / x) for x in [
    "Platforms/Xiaomi", "Common/Mu", "Common/Mu_OEM_Sample",
    "Mu_Basecore", "Silicon/Silicium", "Silicon/Qualcomm"])
e["PATH"] = os.pathsep.join([str(v), str(base / "BinWrappers/PosixLike"),
                             str(base / "Source/C/bin"), e["PATH"]])
conf = w / "Conf"
conf.mkdir(exist_ok=True)
for src, dst in [("target.template", "target.txt"), ("tools_def.template", "tools_def.txt"),
                 ("build_rule.template", "build_rule.txt")]:
    if not (conf / dst).exists():
        shutil.copy2(base / "Conf" / src, conf / dst)
command = [str(v / "python"), str(base / "Source/Python/build/build.py"),
    "-a", "AARCH64", "-b", "RELEASE", "-t", "CLANGPDB",
    "-p", "Platforms/Xiaomi/thorPkg/thor.dsc", "-n", "8",
    "-D", "ENABLE_SECUREBOOT=0", "-D", "FD_BASE=0xA7100000",
    "-D", "FD_SIZE=0x300000", "-D", "FD_BLOCKS=0x300",
    "-D", "MEMORY_PROTECTION=TRUE", "-D", "SHIP_MODE=FALSE",
    "-D", "BUILDID_STRING="+a.build_id,
    "-y", str(l / "build-report.txt"), "-Y", "PCD", "-Y", "LIBRARY", "-Y", "FLASH"]
# Dry run is a pure inspection; it neither changes source nor creates outputs.
if a.dry_run:
    print(json.dumps({"source":str(w),"run_id":a.run_id,"build_id":a.build_id,
                      "command":command,"run_dir":str(l),"phone_actions":0},indent=2))
    raise SystemExit(0)
l.mkdir(parents=True, exist_ok=False)
now = lambda: datetime.datetime.now().astimezone().isoformat()
state = {"state": "building", "started_at": now(), "command": command,
         "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=w, text=True).strip(),
         "source_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=w, text=True),
         "phone_actions": 0}
if state["source_status"]:
    raise RuntimeError("UEFI source has local changes: refusing unpinned build")
status = l / "status.json"
status.write_text(json.dumps(state, indent=2) + "\n")
try:
    with (l / "build.log").open("x") as f:
        cp = subprocess.run(command, cwd=w, env=e, stdout=f, stderr=subprocess.STDOUT, timeout=1800)
    state.update(returncode=cp.returncode, state="built" if cp.returncode == 0 else "error")
except Exception as ex:
    state.update(state="error", error=repr(ex))
state["finished_at"] = now()
status.write_text(json.dumps(state, indent=2) + "\n")
raise SystemExit(0 if state["state"] == "built" else 1)
