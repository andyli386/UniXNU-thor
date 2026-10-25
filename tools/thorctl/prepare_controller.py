#!/usr/bin/env python3
"""Generate sealed one-shot Thor hardware-controller scripts from verified templates.

No phone I/O: only prepares per-run pinned scripts and host preflight manifest.
"""
import argparse,ast,datetime,hashlib,json,re,subprocess
from pathlib import Path


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()


def replace_once(source,old,new,name):
    if source.count(old)!=1:
        raise RuntimeError(f"{name}: expected one occurrence of {old!r}, got {source.count(old)}")
    return source.replace(old,new)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--run-id",required=True)
    ap.add_argument("--source",type=Path,required=True)
    args=ap.parse_args()
    root=args.root.resolve()
    if not re.fullmatch(r"thor-[a-z0-9-]{8,90}",args.run_id):
        raise RuntimeError("Invalid single-use run ID")
    srcrel=Path(args.source)
    source=(root/srcrel).resolve()
    assert source.is_relative_to(root/"worktrees/uefi")
    out=root/"runs"/args.run_id/"l2/controller"
    l3=root/"runs"/args.run_id/"l3"
    image=root/"runs"/args.run_id/"l2/package/thor-boot.img"
    offline=root/"runs"/args.run_id/"l2/package/independent-offline.json"
    assert not out.exists() and not l3.exists()
    assert image.is_file() and offline.is_file()
    assert not (image.parent/"INVALID-NOT-FOR-PHONE").exists()
    assert not (image.parent/"ONE_SHOT_USED-DO-NOT-BOOT-AGAIN").exists()
    checked=json.loads(offline.read_text())
    assert checked["result"]=="PASS" and checked["ready_for_one_use_temporary_boot"]
    assert checked["hardware_test_attempts"]==0
    assert checked["inputs"]["android_boot_v4"]["sha256"]==sha(image)
    assert image.stat().st_size==25894912
    uefi_head=subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD"],text=True).strip()
    assert not subprocess.check_output(["git","-C",str(source),"status","--porcelain"],text=True).strip()
    receipt=json.loads((root/"runs/thor-20261025-vm-uefi-001/l2/firmware-release.json").read_text())
    assert uefi_head==receipt["uefi_commit"]
    refs={
        "fresh_held_usb_entry10g_attempt.py":"fresh_usb_attempt.py",
        "start_thor_entry10g_i386_once.py":"start_once.py",
        "monitor_thor_entry10g_i386.py":"monitor_android.py"
    }
    old_c=json.loads((root/"analysis/entry10g-build/controller-preflight.json").read_text())
    for name in refs:
        p=root/"scripts"/name
        assert sha(p)==old_c["files"][name],"Previously reviewed source altered: "+name
    marker="vmhw01"
    def common(src):
        # The read-only Android monitor deliberately contains no image path.
        if "artifacts/thor-entry-10g/firmware/thor-entry-10g.img" in src:
            src=replace_once(src,"artifacts/thor-entry-10g/firmware/thor-entry-10g.img",
                             f"runs/{args.run_id}/l2/package/thor-boot.img","image path")
        src=replace_once(src,"analysis/hardware-tests/20261025-i386-entry10g-01",
                         f"runs/{args.run_id}/l3","run path")
        src=src.replace("entry10g",marker).replace("ENTRY10G",marker.upper())
        src=src.replace("57666e6ddaa53633cf3e925cf4fa5feeae220e9e424ee1369e071e4c05c3e56d",
                        checked["inputs"]["android_boot_v4"]["sha256"])
        return src
    old_runner=(root/"scripts/fresh_held_usb_entry10g_attempt.py").read_text()
    runner=common(old_runner)
    assert "RUN=ROOT/'runs/"+args.run_id+"/l3'" in runner
    assert "IMAGE=ROOT/'runs/"+args.run_id+"/l2/package/thor-boot.img'" in runner
    assert runner.count("command=[str(FASTBOOT),'--unbuffered','-s',SERIAL,'boot',str(IMAGE)]")==1
    assert runner.count("subprocess.Popen(command,")==1
    assert runner.count("time.monotonic()+45")==1
    assert runner.count("session_identity(")>=2
    old_wrapper=(root/"scripts/start_thor_entry10g_i386_once.py").read_text()
    wrapper=common(old_wrapper)
    wrapper=replace_once(wrapper,"scripts/fresh_held_usb_"+marker+"_attempt.py",
                         f"runs/{args.run_id}/l2/controller/fresh_usb_attempt.py","runner path")
    wrapper=replace_once(wrapper,"analysis/"+marker+"-build/offline-verification.json",
                         f"runs/{args.run_id}/l2/package/independent-offline.json","offline proof path")
    wrapper=replace_once(wrapper,"sources/uefi-thor-"+marker,
                         str(srcrel),"UEFI source path")
    wrapper=replace_once(wrapper,"129054cb66c7adc564336c5553f4cd555c31d108",
                         uefi_head,"UEFI source revision")
    wrapper=replace_once(wrapper,old_c["files"]["fresh_held_usb_entry10g_attempt.py"],
                         hashlib.sha256(runner.encode()).hexdigest(),"runner digest")
    wrapper=replace_once(wrapper,old_c["offline_proof_sha256"],
                         sha(offline),"offline proof digest")
    before=" assert IMAGE.stat().st_size==25894912\n"
    assert wrapper.count(before)==1
    wrapper=wrapper.replace(before,''' assert IMAGE.stat().st_size==25894912
 assert not (IMAGE.parent/'ONE_SHOT_USED-DO-NOT-BOOT-AGAIN').exists()
 assert not (IMAGE.parent/'INVALID-NOT-FOR-PHONE').exists()
''')
    assert "RUNNER=R/'runs/"+args.run_id+"/l2/controller/fresh_usb_attempt.py'" in wrapper
    assert "CHECK=R/'runs/"+args.run_id+"/l2/package/independent-offline.json'" in wrapper
    assert wrapper.count("'reboot','bootloader'")==1
    assert wrapper.count("subprocess.Popen(['/usr/bin/python3','-I',str(RUNNER)]")==1
    assert "source=R/'"+str(srcrel)+"'" in wrapper
    monitor=common((root/"scripts/monitor_thor_entry10g_i386.py").read_text())
    assert "out=r/'runs/"+args.run_id+"/l3'" in monitor
    assert monitor.count("'getprop'")==1
    files={"fresh_usb_attempt.py":runner,"start_once.py":wrapper,"monitor_android.py":monitor}
    for name,text in files.items():
        assert not re.search(r"(?i)['\"](?:flash|erase|format|set_active|unlock|lock)['\"]",text)
        assert "thor-entry-10g/" not in text
        assert "20261025-i386-entry10g-01" not in text
        ast.parse(text,filename=name)
    assert not l3.exists()
    out.mkdir(parents=True)
    digests={}
    for name,script in files.items():
        path=out/name
        with path.open("x") as f:f.write(script)
        digests[name]=sha(path)
        compile(script,str(path),"exec")
    record={
        "schema":"thor-run-controller-preflight-v2",
        "created_at":datetime.datetime.now().astimezone().isoformat(),
        "status":"READY_NOT_EXECUTED","result":"PASS_HOST_CONTROLLER_REVIEW",
        "run_id":args.run_id,"image_sha256":sha(image),"image_bytes":image.stat().st_size,
        "source_commit":uefi_head,"offline_proof_sha256":sha(offline),
        "files":digests,"parent_reviewed_script_sha256":old_c["files"],
        "max_adb_reboots":1,"max_fastboot_boots":1,
        "automatic_retries":0,"no_flash_erase_format_or_slot":True,
        "fresh_usb_bound_45_sec":True,"read_only_fastboot_gate":True,
        "real_image_paths_checked":True,"real_source_paths_checked":True,
        "phone_actions":0,"recording_confirmed":False,
        "manual_start_only":True
    }
    with (out/"controller-preflight.json").open("x") as f:json.dump(record,f,indent=2);f.write("\n")
    print(json.dumps({"result":record["result"],"run_id":args.run_id,
          "controller":str(out),"files_sha256":digests,
          "image_sha256":record["image_sha256"],"phone_actions":0},indent=2))


if __name__=="__main__":
    main()
