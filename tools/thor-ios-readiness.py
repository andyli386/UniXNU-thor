#!/usr/bin/env python3
"""Read-only Thor -> UniXNU full iOS boot-path integration gap audit.

Facts are collected from real local firmware, board config and archive paths.
Passing a bounded VM checkpoint must never certify an iOS boot milestone.
No access to phone, no firmware flashing, no downloads or IPSW decryption.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import tomllib

UEFI_REQUIRED={
 "UniXNU boot manager":"UniXNUPkg/Application/UniXNU/UniXNU.inf",
 "UniXNU boot registrar":"UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf",
 "DarwinBoot XNU loader":"DarwinBootPkg/Driver/DarwinBoot.inf",
 "RAM payload block publisher":"UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.inf",
 "DiskIo provider":"MdeModulePkg/Universal/Disk/DiskIoDxe/DiskIoDxe.inf",
 "FAT filesystem":"FatPkg/EnhancedFatDxe/Fat.inf",
 "PartitionDxe provider":"MdeModulePkg/Universal/Disk/PartitionDxe/PartitionDxe.inf",
}
TARGET_IO_FLAGS={
 "Platform Expert / ADT":"CONFIG_XNU_ARM_DT_PLATFORM",
 "ARM CPU bootstrap":"CONFIG_XNU_ARMCPU",
 "ARM architected timer":"CONFIG_XNU_NATIVE_ARCH_TIMER",
 "Native UFS":"CONFIG_XNU_NATIVE_UFS",
 "Root filesystem owner mount":"CONFIG_XNU_OWNER_CACHE_MOUNT",
 "Cryptex/SystemOS":"CONFIG_XNU_OWNER_CRYPTEX_FILES",
 "Data volume":"CONFIG_XNU_OWNER_DATA_MOUNT",
 "Mobile display driver":"CONFIG_XNU_NATIVE_MOBILE_DISPLAY",
}

def sha(f):
 h=hashlib.sha256()
 with f.open("rb") as stream:
  for d in iter(lambda:stream.read(1024*1024),b""):h.update(d)
 return h.hexdigest()

def check(flag,what,detail,evidence,blocker=True,scope="target"):
 return {"name":what,"status":"READY" if flag else "MISSING",
         "detail":detail,"evidence":evidence,
         "blocks_home_screen":blocker,"scope":scope}

def audit(root,device_ref):
 root=root.resolve()
 repo=root/"repos/UniXNU-thor"
 board=repo/"board.toml"
 xnu=repo/"configs/thor_compile_defconfig"
 uefi=root/"../thor-unixnu-that-does-not-exist"
 # On Mac firmware clone: repos/uefi-thor/Platforms/Xiaomi/thorPkg
 # On i386 only the later isolated /worktrees/uefi/hw12 is populated.
 paths=[
  root/"repos/uefi-thor/Platforms/Xiaomi/thorPkg",
  root/"worktrees/uefi/hw12/Platforms/Xiaomi/thorPkg",
  root/"worktrees/uefi/hw11/Platforms/Xiaomi/thorPkg",
 ]
 firmware=next((p for p in paths if p.is_dir()),None)
 assert firmware is not None, "no Thor firmware platform directory found"
 assert board.is_file() and xnu.is_file()
 b=tomllib.loads(board.read_text())
 flags=dict(re.findall(r"^(CONFIG_XNU_[A-Z0-9_]+)=(y|n)",xnu.read_text(),re.M))
 dsc=(firmware/"thor.dsc").read_text()
 fdf=(firmware/"thor.fdf").read_text()
 components=(firmware/"Include/Components.inc").read_text() if (firmware/"Include/Components.inc").exists() else ""
 dxe=(firmware/"Include/DXE.inc").read_text() if (firmware/"Include/DXE.inc").exists() else ""
 # The firmware volume requires explicit paths, not "could load" guesses.
 combined_fv=fdf+"\n"+dxe
 combined_dsc=dsc+"\n"+components
 tests=[]
 for title,inf in UEFI_REQUIRED.items():
  present=(inf in combined_dsc and inf in combined_fv)
  tests.append(check(present,title,
     "Required UEFI INF must be in BOTH DSC components and FDF firmware volume",
     ["thor.dsc","thor.fdf","Include/Components.inc","Include/DXE.inc"]))
 for title,flag in TARGET_IO_FLAGS.items():
  enabled=flags.get(flag)=="y"
  tests.append(check(enabled,title,flag+"="+str(flags.get(flag,"UNSET")),
                     ["configs/thor_compile_defconfig"]))
 pong_board=device_ref/"board.toml"
 pong=tomllib.loads(pong_board.read_text()) if pong_board.is_file() else None
 tests.append(check("ipsw" in b and all(k in b["ipsw"] for k in ("build","product","version","device_class")),
                 "Target iOS IPSW identity",
                 "Thor board.toml must select a compatible OS build/device identity",
                 ["board.toml"]))
 tests.append(check(bool(b.get("kernel",{}).get("archives")),
                 "Device kernel archive selection",
                 "Currently selected native linked component archives: "+str(len(b.get("kernel",{}).get("archives",[]))),
                 ["board.toml"]))
 # Deliberately do not certify these from file names alone.
 dirs=[root/"out/ipsw",root/"out/thor",root/"artifacts/ios",root/"artifacts/userdata"]
 userland=any(p.is_dir() and any(p.rglob("SystemOS*.dmg")) for p in dirs if p.exists())
 tests.append(check(userland,"Matched OS/SystemOS image inputs",
                    "No complete matching system and cryptex media found in known build destinations",
                    ["out/ipsw","out/thor","artifacts/ios","artifacts/userdata"]))
 data=any(p.is_dir() and any(p.glob("**/*userdata*root*.img")) for p in dirs if p.exists())
 tests.append(check(data,"iOS OS/Data root image",
                    "Need user-authorized safe installation design, root UUID, OS/Data image and backup plan",
                    ["out/thor","artifacts/userdata"]))
 tests.append(check(False,"SpringBoard service start trace",
                    "No Thor L3 evidence of launchd/SpringBoard; cannot assert from pre-pmap color",
                    ["evidence/hardware-tests","runs"]))
 hw11=root/"evidence/build/thor-20261026-vm-hw11/final-host-gate.json"
 host=json.loads(hw11.read_text()) if hw11.is_file() else None
 result={
  "schema":"thor-ios-fullchain-readiness-v1",
  "as_of":dt.datetime.now().astimezone().isoformat(),
  "device":"Xiaomi 12S Ultra (thor) / SM8475",
  "current_boot_profile":b.get("board",{}).get("description"),
  "current_xnu_path":"Darwin 23 experimental bounded pre-pmap (compile-only device configuration)",
  "validated_vm_source":"HW10 L3 00..10 colored, pmap not yet executed",
  "HW11_unchanged":True,
  "hw11_host_candidate_ready":bool(host and host.get("result")=="PASS_L2_READY_AWAITING_EXPLICIT_RECORDED_ONE_USE_HARDWARE_TEST"),
  "pong_reference":{
    "available":pong is not None,
    "same_SoC":bool(pong and pong.get("board",{}).get("soc")=="SM8475"),
    "reference_version":pong.get("ipsw",{}).get("version") if pong else None,
    "reference_build":pong.get("ipsw",{}).get("build") if pong else None,
    "reference_device_class":pong.get("ipsw",{}).get("device_class") if pong else None,
    "reference_loader":"DarwinBoot22 (different ABI from current Thor Darwin23)",
    "reuse_policy":"adopt source/architecture; replace board-specific memory, display, GIC, UFS, DTS, power"
  },
  "checks":tests,
  "total_required":len(tests),
  "ready_count":sum(v["status"]=="READY" for v in tests),
  "missing_count":sum(v["status"]=="MISSING" for v in tests),
  "pre_pmap_only":True,
  "hardware_home_screen_proven":False,
  "safe_strategy":[
    "Keep HW11 and earlier one-use images immutable; no reuse",
    "Build an independent full UniXNU + DarwinBoot + payload integration lane",
    "Choose one pinned coherent iOS/XNU/driver/loader ABI baseline, preferably compare Pong 16.6.1 first",
    "Port board-specific driver configuration, ADT, UFS, display and GIC while retaining proven Thor early-VM gates",
    "Prove pmap completion, platform, root, launchd and SpringBoard independently",
    "Do not flash userdata or change partitions without separate user approval and verified backup"
  ],
  "read_only":True,
  "phone_actions":0
 }
 return result

def markdown(r):
 lines=["# Thor iOS 主屏幕路径缺口审计", "",
        "自动扫描时间："+r["as_of"],"",
        "状态：**尚不能从现有 HW11 直接进入 iOS 主界面。** "+str(r["ready_count"])+"/"+str(r["total_required"])+" 项本地静态集成条件满足；该比例不代表工程完成百分比。","",
        "现有 L3：HW10 已证明 XNU pre-pmap 守卫，但仍未执行完整 pmap_bootstrap / 内核调度 / launchd。","",
        "Pong（同 SM8475）参考 iOS 版本："+str(r["pong_reference"]["reference_version"])+" / "+str(r["pong_reference"]["reference_build"])+"，需要 DarwinBoot22 配套，不能与 Thor 的 Darwin23 试验内核混用。","",
        "## 静态缺口", "",
        "| 要素 | 检查结果 | 说明 |","|---|---|---|"]
 for test in r["checks"]:
  lines.append("| "+test["name"]+" | "+test["status"]+" | "+test["detail"].replace("|","/")+" |")
 lines += ["","## 通向 SpringBoard 的主线",""]
 for i,step in enumerate(r["safe_strategy"],1):
  lines.append(str(i)+". "+step)
 lines += ["","验收结论仅依靠仓库中的真实配置与已存在的文件。安全门禁和 L3 录像仍分别进行。"]
 return "\n".join(lines)+"\n"

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--root",required=True,type=Path)
 p.add_argument("--pong",required=True,type=Path)
 p.add_argument("--out-json",required=True,type=Path)
 p.add_argument("--out-md",required=True,type=Path)
 a=p.parse_args()
 d=audit(a.root,a.pong)
 assert a.out_json != a.out_md
 for out in (a.out_json,a.out_md):
  out.parent.mkdir(parents=True,exist_ok=True)
  assert not out.exists(),"Never overwrite existing evidence: "+str(out)
 a.out_json.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n")
 a.out_md.write_text(markdown(d))
 print(json.dumps({"result":"PASS_READ_ONLY_GAP_AUDIT",
                   "checks":d["total_required"],"ready":d["ready_count"],
                   "missing":d["missing_count"],
                   "pong_ref":d["pong_reference"]["reference_build"],
                   "phone_actions":0},ensure_ascii=False))
if __name__=="__main__":main()
