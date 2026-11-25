"""Replay the v219 handset capture and test coexisting legacy and v220 stores."""
from pathlib import Path
import copy
import hashlib
import json
import os
import runpy
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

work = Path(__file__).resolve().parent
parent = work.parent
real = parent / "recovery-v219-history-20261124T060551Z.DG7hLm"
old = "9293BEC1-E324-48F0-94BA-1B0BDE0C66E8"
trial = "CAB00000-0000-4000-B000-000000000227"
newer = "20200000-0000-4000-B000-000000000202"
sha = lambda b: hashlib.sha256(b).hexdigest()
def metadata(path, data):
    return {"path": path, "exists": True, "bytes": len(data), "sha256": sha(data),
            "crash_replay":{"state":"full", "recovered":{"offset":0,"length":len(data),"sha256":sha(data)}}}
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    subprocess.run([sys.executable, "-IB", str(work/"prepare.py"), str(root)], check=True)
    parser = runpy.run_path(str(root/"freeze-evidence.py"))
    inv = runpy.run_path(str(root/"history.py"))["inventory"]
    baseline = runpy.run_path(str(parent/"v210-app-stall/freeze-evidence.py"))["report"]
    assert parser["report"](real,old) == baseline(real,old)
    manifest = json.loads((real/"manifest.json").read_bytes())
    assert inv(real,manifest["volumes"]["Data"]["files"])["corroborated_v227"] == []
    vm = manifest["volumes"]["Data"]
    rows = vm["freeze_files"]
    for row in rows:
        if not row.get("sha256"):continue
        dest = root/"Data"/row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(real/"Data"/row["path"])
    newerows=[]
    for row in rows:
        name=row["path"].replace("/ThorStall-","/ThorStall-v220-").replace("/ThorSwipeStack-","/ThorSwipeStack-v220-")
        newerows.append({"path":name,"exists":False})
    full=next(r for r in rows if r["path"].endswith("/ThorStall-SpringBoard.bin") and r.get("sha256"))
    data=bytearray((real/"Data"/full["path"]).read_bytes())
    changed=0
    for off in range(0,len(data)-511,512):
        block=bytes(data[off:off+512])
        if block[32:68] == old.encode() and struct.unpack_from("<I",block,508)[0]==zlib.crc32(block[:508]):
            data[off:off+508]=block[:508].replace(old.encode(),trial.encode())
            struct.pack_into("<I",data,off+508,zlib.crc32(data[off:off+508]))
            changed+=1
    assert changed>0
    dst=full["path"].replace("/ThorStall-","/ThorStall-v220-")
    (root/"Data"/dst).write_bytes(data)
    j=next(i for i,r in enumerate(newerows) if r["path"]==dst);newerows[j]=metadata(dst,data)
    stack=next(name for name,item in parser["report"](real,old)["files"].items()
               if item.get("snapshot",{}).get("state")=="complete" and item["snapshot"].get("requested_boot"))
    odst=stack.replace("/ThorSwipeStack-","/ThorSwipeStack-v220-")
    payload=(real/"Data"/stack).read_bytes().replace(old.encode(),trial.encode())
    (root/"Data"/odst).write_bytes(payload)
    j=next(i for i,r in enumerate(newerows) if r["path"]==odst);newerows[j]=metadata(odst,payload)
    vm["freeze_files"]=rows+newerows;vm["freeze_schema"]=220
    (root/"manifest.json").write_text(json.dumps(manifest))
    new_report=parser["report"](root,trial)
    assert len(new_report["events"])==changed and all("-v220-" in e["file"] for e in new_report["events"])
    assert new_report["files"][odst]["snapshot"]["state"]=="complete" and new_report["files"][odst]["snapshot"]["requested_boot"]
    assert parser["report"](root,old)["events"]==baseline(real,old)["events"]
    print("PASS v219 exact report replay and mixed legacy/v220 journal and full-stack names; CRC and boot filtering")
    vm["freeze_files"].pop();(root/"manifest.json").write_text(json.dumps(manifest))
    try: parser["report"](root,trial)
    except ValueError: pass
    else: raise AssertionError("incomplete v220 inventory was accepted")
    crows=[]
    def console(family,verified):
        rel="logs/"+family+".txt"
        b=(f"Pong console capture BEGIN boot={trial} pid=10 first=0\n".encode()+
           (b"[ 10.000000]: THOR CONSOLE V205: staged /private/var/thor-console-v227/libpong-console-v205.dylib bytes=100\n" if verified else b"[ 10.000000]: unrelated\n")+
           f"Pong console capture BEGIN boot={newer} pid=11 first=0\n".encode())
        out=root/"Data"/rel;out.parent.mkdir(exist_ok=True,parents=True);out.write_bytes(b);return metadata(rel,b)
    crows=[console("PongConsole",True),console("PongConsole-v172",False)]
    assert inv(root,crows)["corroborated_v227"]==[]
    crows[1]=console("PongConsole-v172",True)
    result=inv(root,crows)
    assert result["corroborated_v227"]==[trial] and set(result["latest"].values())=={newer}
    print("PASS later legacy boot, two independent console histories, incomplete inventory rejection")
