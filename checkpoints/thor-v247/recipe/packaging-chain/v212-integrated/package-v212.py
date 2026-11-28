#!/opt/homebrew/bin/python3.13
"""Build and verify the unified hardware display, CPU and IPCRTR image."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import tomllib

root = Path(__file__).resolve().parent
base = root.parent/"v211-cpu-demand"
loader = runpy.run_path(str(base/"package-v211.py"))
decode,util,pack,sha,filehash,k,m,c = [loader[n] for n in ("dec","util","pack","sha","fh","k","m","c")]
api = runpy.run_path(str(root.parent/"v206-cellular/trial-image/package-probe.py"))["api"]
device_tree = k+".adt"
selectors = ("thor-pas4-probe","thor-glink-version","thor-glink-ipcrtr")
enabled = (1).to_bytes(4,"little")
parent_sha = "362a67426ebc096007ea1ee54210f896d68dd609a6ef520391ce189f8cbe6f3a"
kernel = root/"kernel/kernel-ipados18-thor.macho"
turnip = "libvulkan_freedreno.dylib"
bridge = "PongMetalBridge.dylib"
diag = "libpong-console-v205.dylib"
# Keep the already tested in-image ID and title so the release is byte-identical.
target = root/"boot-thor-unixnu-v212-unified.img"
record = root/"unified-verification.json"
embedded_id = "v212-cellular"
read = lambda p: json.loads(p.read_bytes())

def configuration():
    parent = read(base/"verification.json")
    assert read(base/"replayed.json")==parent
    old,fw,sig,id_ = decode(base/parent["image"])
    assert parent_sha == id_ == parent["image_sha256"]
    assert parent["kernel_sha256"] == sha(old[k])
    current = read(root/"runtime/final-inputs.json")
    title = "Thor v212 - Display and IPCRTR channel trial"
    menu,args = pack["new_menu"](old[c], {"title":title,"replace_args":{}})
    manifest = json.loads(old[m])
    assert manifest["boot_args"] == args == parent["boot_args"]
    assert len(args.encode()) == 1022
    nodes = api["parse_adt"](old[device_tree])
    assert set(selectors).isdisjoint(nodes["/chosen"])
    assert bytes(api["emit"](nodes,"/")) == old[device_tree]
    selection = dict.fromkeys(selectors, 1)
    for name in selectors:
        nodes["/chosen"][name] = enabled
    dt = bytes(api["emit"](nodes,"/"))
    assert api["parse_adt"](dt) == nodes
    assert dt!=old[device_tree]
    # The v205 manifest carried software-only diagnostic fields through v211.
    # Replace them from the signed payload inventory for this exact image.
    manifest.update(version=212,release_variant=embedded_id,purpose=title,candidate_parent=parent_sha,
        kernel_source=str(kernel),kernel_sha256=filehash(kernel),adt_sha256=sha(dt),
        runtime_directory=current["pool_path"],runtime_source="v212 signed Turnip and v209 signed Metal with v210 launch collection",
        gpu_private_boot=False,graphics_runtime_active=True,diagnostic_stage_path=current["diagnostic_stage_path"],
        config_change={"bootargs_changed":False,"thor_cpu_default":1,"chosen_selectors":selection},
        modem_trial={"requested":True,"features":0,"selected":selection,"hardware_verified":False},
        candidate_change=["Avoid finished framebuffer color sampling, index GPU descriptor views, propagate wait-any errors.",
                          "Defer default modem power before load-on without ICC. Opt-in to bounded signed GLINK IPCRTR trial."],
        hardware_validated=False,flash_performed=False,fastboot_boot_performed=False)
    for label,name in (("bridge",bridge),("turnip",turnip),("diagnostic",diag)):
        for field in ("sha256","uuid"):
            manifest[label+"_"+field]=current["payloads"][name][field]
    changes={k:kernel.read_bytes(),m:util["json_bytes"](manifest),c:menu}
    if dt!=old[device_tree]: changes[device_tree]=dt
    return parent,old,fw,sig,current,dt,changes

def verify(path):
    source=read(root/"prepare/source-lock.json")
    for evidence,digest in source["files"].items(): assert filehash(root/evidence)==digest,evidence
    assert (root/"prepare/build.status").read_text().strip()=="0"
    assert (root/"prepare/admission.txt").read_text().strip()=="96 ARM64 ASan/UBSan admission scenarios passed"
    parent,old,fw,sig,rt,dt,wanted=configuration()
    data,newfw,newsig,digest=decode(path)
    assert old.keys()==data.keys() and (newfw,newsig)==(fw,sig)
    assert {n for n in old if old[n]!=data[n]}==set(wanted)
    assert all(data[n]==blob for n,blob in wanted.items()) and data[device_tree]==dt
    cfg=tomllib.loads(data[c].decode())
    man=json.loads(data[m])
    pack["MENU"]["verify_abi"]()
    chosen=pack["MENU"]["validate_menu"](cfg,man)
    assert cfg["boot"]=={"default":"xnu","timeout":2} and chosen[0]["argument_bytes"]==1022
    assert {"pong_metal=1","pong_gpu_scanout=1","pong_adreno_start=1","fb_hz=120"} <= set(man["boot_args"].split())
    assert all(not w.startswith(("pas4trial=","pong_ui_software=")) for w in man["boot_args"].split())
    actual=data[k]
    for token in (b"THOR CPU V211: governor=load",b"THOR TARGET EXEC V210:",b"THOR GLINK V209:",
                  b"THOR MODEM LOAD V212: deferred before load-on",b"thor-glink-ipcrtr",b"IOAcceleratorES\0"):
        assert token in actual, token
    for value in (rt["pool_path"]+"/"+bridge,rt["pool_path"]+"/"+turnip,rt["diagnostic_stage_path"]):
        assert value.encode()+b"\0" in actual,value
    assert parent["pool_path"].encode()+b"/"+turnip.encode()+b"\0" not in actual
    inventory={}
    for name,prior in parent["runtime"].items():
        src=root/"runtime"/name if name!="thor-apps-install" else root.parent/"v151-balanced-rows/runtime"/name
        content=src.read_bytes()
        expected=rt["payloads"].get(name,prior)
        assert sha(content)==expected["sha256"] and len(content)==expected["bytes"] and actual.count(content)==1,name
        assert (sha(content)!=prior["sha256"])==(name==turnip)
        assert loader["v"]["abi"](content)["uuid"]==expected["uuid"]
        subprocess.run(["codesign","--verify","--strict",src],check=True)
        inventory[name]={"sha256":sha(content),"uuid":expected["uuid"],"bytes":len(content),"offset":actual.index(content)}
    ids={subprocess.check_output(["xcrun","dwarfdump","--uuid",root/"kernel"/n],text=True).split()[1]
         for n in ("kernel-ipados18-thor.macho","mach.development.pong","mach.development.pong.unstripped")}
    assert len(ids)==1 and parent["kernel_uuid"] not in ids
    _,off,size,_=util["sections"](path)
    with tempfile.NamedTemporaryFile(dir=root,suffix=".fat") as tmp,path.open("rb") as f:
        f.seek(off);tmp.write(f.read(size));tmp.flush()
        subprocess.run(["/sbin/fsck_msdos","-n",tmp.name],check=True,stdout=subprocess.DEVNULL)
    return {"version":212,"variant":"unified","embedded_release_id":man["release_variant"],"image":target.name,"image_bytes":path.stat().st_size,
            "image_sha256":digest,"kernel_uuid":ids.pop(),"kernel_sha256":sha(actual),"parent_sha256":parent_sha,
            "runtime":inventory,"pool_path":rt["pool_path"],"diagnostic_path":rt["diagnostic_stage_path"],
            "adt_sha256":sha(dt),"chosen_selectors":man["config_change"]["chosen_selectors"],"boot_args":man["boot_args"],
            "firmware_sha256":fw,"signature_sha256":sig,"changed_fat_members":sorted(wanted),
            "source_lock_sha256":filehash(root/"prepare/source-lock.json"),"hardware_validated":False}

def build(tmp):
    changes=configuration()[-1]
    epoch=1795428802
    assert filehash(util["FULL"])==util["PARENT_FULL_SHA"]
    full=tmp/"full.img";dest=tmp/"compact.img"
    shutil.copyfile(util["FULL"],full)
    env=dict(os.environ,TZ="UTC",SOURCE_DATE_EPOCH=str(epoch))
    for index,(name,content) in enumerate(changes.items()):
        f=tmp/str(index);f.write_bytes(content);os.utime(f,(epoch,epoch))
        subprocess.run(["mcopy","-m","-o","-i",util["volume"](full),f,"::"+name],check=True,env=env)
    util["compact"](full,dest)
    return dest

if __name__=="__main__":
    assert __debug__, "verification requires Python assertions"
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("package","verify","replay"))
    a=parser.parse_args()
    if a.action=="verify" or (a.action=="package" and target.exists()):
        result=verify(target);assert result==read(record)
    else:
        with tempfile.TemporaryDirectory(dir=root,prefix=".package-") as dirname:
            d=Path(dirname);temporary=build(d);result=verify(temporary)
            if a.action=="replay":
                assert result==read(record) and filehash(temporary)==filehash(target)
            else:
                (d/"receipt").write_bytes(util["json_bytes"](result))
                os.link(temporary,target);os.replace(d/"receipt",record)
    print(util["json_bytes"](result).decode(),end="")
