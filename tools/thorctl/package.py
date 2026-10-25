#!/usr/bin/env python3
"""Create one private ENTRY10 Android Boot v4 candidate, with no phone commands."""
from pathlib import Path
import datetime, gzip, hashlib, json, os, struct, subprocess, argparse, tomllib

os.umask(0o077)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root",type=Path,default=Path("/home/vincent/Projects/thor-unixnu"))
parser.add_argument("--source",type=Path,required=True)
parser.add_argument("--kernel",type=Path,required=True)
parser.add_argument("--fd",type=Path,required=True)
parser.add_argument("--fd-audit",type=Path,required=True)
parser.add_argument("--firmware-receipt",type=Path,required=True)
parser.add_argument("--provenance",type=Path,required=True)
parser.add_argument("--run-id",required=True)
parser.add_argument("--dry-run",action="store_true")
args=parser.parse_args()
ROOT=args.root.resolve()
def rooted(p):
    x=(ROOT/p).resolve()
    if not x.is_relative_to(ROOT):raise RuntimeError("Path escapes workspace")
    return x
WORK=rooted(args.source)
KERNEL=rooted(args.kernel)
FD=rooted(args.fd)
AUDIT=rooted(args.fd_audit)
RECEIPT=rooted(args.firmware_receipt)
ADT=WORK/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
PROVENANCE=rooted(args.provenance)
BASE=ROOT/"artifacts/thor-smoke-01"
if not __import__("re").fullmatch(r"[a-z][a-z0-9-]{6,96}",args.run_id):
    parser.error("Invalid unique run ID")
OUT=rooted(Path("runs")/args.run_id/"l2/package")
config=tomllib.loads((ROOT/"thor-build.toml").read_text())
BASE_KERNEL_PIN=config["stock"]["verified_entry09_kernel_sha256"]
frozen=json.loads((KERNEL.parent/"build-manifest.json").read_text())
KERNEL_PIN=frozen["kernel_sha256"]
fw_receipt=json.loads(RECEIPT.read_text())
FD_PIN=fw_receipt["uefi_fd_sha256"]
SOURCE_PIN=fw_receipt["uefi_commit"]
def digest(data): return hashlib.sha256(data).hexdigest()
def now(): return datetime.datetime.now().astimezone().isoformat()
def write(path,data):
    with path.open("xb") as f: f.write(data)
def check(cond,message):
    if not cond: raise RuntimeError(message)

def main():
    check(os.sys.platform.startswith("linux"),"Linux seed source required")
    check(subprocess.check_output(["git","-C",str(WORK),"rev-parse","HEAD"],text=True).strip()==SOURCE_PIN,"UEFI source revision")
    check(not subprocess.check_output(["git","-C",str(WORK),"status","--porcelain"],text=True).strip(),"UEFI source unclean")
    kernel=KERNEL.read_bytes()
    check(len(kernel)==frozen["kernel_bytes"] and digest(kernel)==KERNEL_PIN,"Frozen kernel identity")
    fd=FD.read_bytes()
    check(len(fd)==3145728 and digest(fd)==FD_PIN,"Reviewed FD identity")
    audit=AUDIT
    approved=json.loads(audit.read_text())
    check(approved["status"]=="PASS" and approved["fd"]["sha256"]==FD_PIN and not approved["failures"],"Independent FD audit")
    meta=json.loads((KERNEL.parent/"kernel-layout.json").read_text())
    check(meta["kernel_sha256"]==KERNEL_PIN and meta["loaded_span_bytes"]==20185088 and meta["segment_count"]==10,"Mach-O layout")
    release=json.loads((KERNEL.parent/"build-manifest.json").read_text())
    check(release["xnu_commit"]==frozen["xnu_commit"],"XNU frozen source")
    check(release["kernel_sha256"]==KERNEL_PIN and release["kernel_bytes"]==len(kernel),"Kernel freeze")
    original=(ROOT/"artifacts/mainline-4k-entry09-vmprep-20261024/run02/kernel.entry09-vmprep.macho")
    check(digest(original.read_bytes())==BASE_KERNEL_PIN,"Original ENTRY09 kernel")
    expected=(ADT/"ThorEntry06PayloadExpected.h").read_text()
    check(str(len(kernel))+"ULL" in expected and "ENTRY10" in expected,"UEFI kernel-header binding")
    check(PROVENANCE.is_file() and PROVENANCE.parent==ADT,"Source-provenance input")
    check(digest(PROVENANCE.read_bytes())!=BASE_KERNEL_PIN,"Non-legacy source record")
    canonical=json.loads(PROVENANCE.read_text())
    check(canonical["kernel_sha256"]==KERNEL_PIN and canonical["loaded_span_sha256"]==meta["loaded_span_sha256"],"UEFI layout binding")
    def orig(name,pin):
        value=(BASE/name).read_bytes()
        check(digest(value)==pin,"Stock component changed: "+name)
        return value
    shim=orig("BootShim.bin","9346a527441b242c69414fd7e3d6fa17813f281d562fef13d9ba8373d39007a9")
    dtb=orig("thor-stock-vendor-dtb.bin","c54e1b6efb086420761f05c2358aa71c98db5f961408399c0e1a85a408328971")
    check(len(shim)==112 and len(dtb)==6443651,"Stock boot component size")
    prefix=struct.pack("<16sIIQ32s",b"THOR-PAYLOAD09\0\0",2,128,len(kernel),bytes.fromhex(KERNEL_PIN))
    check(len(prefix)==64 and digest(prefix)==canonical["payload_prefix_sha256"],"Header source identity")
    check(not OUT.exists(),"Refuse to reuse or overwrite an existing attempt")
    if args.dry_run:
        print(json.dumps({"result":"PASS_NO_ARTIFACT_CREATED","kernel_sha256":KERNEL_PIN,
          "fd_sha256":FD_PIN,"uefi_commit":SOURCE_PIN,
          "stock_kernel_sha256":BASE_KERNEL_PIN,"out":str(OUT),"phone_actions":0},indent=2))
        return
    OUT.mkdir(parents=True,mode=0o700)
    try:
        # Generate only after all immutable source and binary gates passed.
        seed=os.getrandom(64,0)
        check(len(seed)==64 and any(seed),"Invalid CSPRNG result")
        length=((max(128+len(kernel),19289048)+4095)//4096)*4096
        bundle=prefix+seed+kernel+bytes(length-128-len(kernel))
        check(len(bundle)==length and bundle.count(b"THOR-PAYLOAD09\0\0")==1,"Wire and size limits")
        write(OUT/"kernel-bundle.bin",bundle)
        write(OUT/"SILICIUM_UEFI.fd",fd)
        write(OUT/"BootShim.bin",shim)
        write(OUT/"thor-stock-vendor-dtb.bin",dtb)
        compressed=gzip.compress(shim+fd,compresslevel=9,mtime=0)
        write(OUT/"kernel.gz-dtb",compressed+dtb)
        metadata={"schema":"thor-one-use-image-v2","created_at":now(),"state":"packaged-awaiting-independent-offline-review","stage":"THOR_PROFILED_ONE_USE_CANDIDATE","kernel_sha256":KERNEL_PIN,"kernel_source_revision":release["xnu_commit"],"uefi_commit":SOURCE_PIN,"fd_sha256":FD_PIN,"fd_audit_sha256":digest(audit.read_bytes()),"bundle_sha256":digest(bundle),"bundle_bytes":len(bundle),"seed_sha256":digest(seed),"seed_source":"linux-os.getrandom(flags=0)","seed_bytes":64,"seed_raw_logged":False,"seed_reuse_allowed":False,"header_magic":"THOR-PAYLOAD09 v2 intentionally reused","full_vm_boot":False,"ttbr1_live_verified":False,"phone_attempts":0}
        write(OUT/"payload-contract.json",(json.dumps(metadata,indent=2)+"\n").encode())
        image=OUT/"thor-boot.img"
        cmds=[
            ["mkbootimg","--header_version","4","--kernel",str(OUT/"kernel.gz-dtb"),"--ramdisk",str(OUT/"kernel-bundle.bin"),"--os_version","12.0.0","--os_patch_level","2024-09","--cmdline","","-o",str(image)],
            ["/usr/bin/python3",str(ROOT/"scripts/verify_android_boot_v4.py"),str(image),"--expected-header-version","4","--kernel-format","gzip-bootshim-fd-dtb","--shim",str(OUT/"BootShim.bin"),"--fd",str(OUT/"SILICIUM_UEFI.fd"),"--dtb",str(OUT/"thor-stock-vendor-dtb.bin"),"--ramdisk",str(OUT/"kernel-bundle.bin"),"--fd-base","0xA7100000","--fd-size","0x300000","--max-image-size","201326592","--strict-padding","--require-exact-size"],
            ["unpack_bootimg","--boot_img",str(image),"--out",str(OUT/"unpacked")],
        ]
        records=[]
        for idx,argv in enumerate(cmds):
            process=subprocess.run(argv,capture_output=True,text=True,timeout=120,check=False)
            label=("mkbootimg","verify-image","unpack-crosscheck")[idx]
            write(OUT/(label+".stdout"),process.stdout.encode())
            write(OUT/(label+".stderr"),process.stderr.encode())
            records.append({"name":label,"argv":argv,"returncode":process.returncode})
            check(process.returncode==0,"Package tool failed: "+label)
        check((OUT/"unpacked/kernel").read_bytes()==(OUT/"kernel.gz-dtb").read_bytes(),"Unpack kernel mismatch")
        check((OUT/"unpacked/ramdisk").read_bytes()==bundle,"Unpack ramdisk mismatch")
        check(image.stat().st_size < 64*1024*1024,"Boot image cap")
        metadata.update(image_sha256=digest(image.read_bytes()),image_bytes=image.stat().st_size,run_id=args.run_id,checks=["source","kernel","FD","FV auditor","seed","wire","Android v4","unpack"],package_commands=records)
        manifest=OUT/"manifest.json"
        write(manifest,(json.dumps(metadata,indent=2)+"\n").encode())
        print(json.dumps({"state":"ONE_USE_IMAGE_HOST_PACKAGED","image":str(image),"sha256":metadata["image_sha256"],"bytes":image.stat().st_size,"seed_logged":False,"phone_actions":0},indent=2))
    except Exception:
        # Leave invalid output intact for analysis, but it MUST NOT be launched.
        (OUT/"INVALID-NOT-FOR-PHONE").write_text(now()+"\n")
        raise
if __name__=="__main__":main()
