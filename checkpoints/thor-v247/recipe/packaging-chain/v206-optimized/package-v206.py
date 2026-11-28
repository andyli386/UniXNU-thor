#!/opt/homebrew/bin/python3.13
"""Assemble the approved v206 kernel with v205's validated firmware and boot menu."""
import argparse, hashlib, json, os, runpy, shutil, subprocess, tempfile, tomllib
from pathlib import Path

root = Path(__file__).resolve().parent
base_dir = root.parent / "v205-software-display"
parent_image = base_dir / "boot-thor-unixnu-v205-software-private-gpu.img"
base_sha = "9cf28b96a1163f1e3bd50a68285b642414861c8f30d40b273f8b62abc1d6bde4"
base_kernel_sha = "4057f2190eb1f7ee5368b1982e316836f061d74b78c963881b410f66eee515d7"
kernel = root / "kernel/kernel-ipados18-thor.macho"
image = root / "boot-thor-unixnu-v206-optimized.img"
record = root / "BUILD-private.json"
p = runpy.run_path(str(root.parent/"v203-display-120hz/package-v203.py"))
F = p["fat"]
sha = p["sha"]; file_hash = p["sha_file"]; read = p["read"]; volume = p["volume"]; dump = p["json_bytes"]
decode = runpy.run_path(str(root/"prepare/fat_audit.py"))["decode"]
kname, mname, cname = "/efi/unixnu/payloads/kernel.macho", "/efi/unixnu/pong-boot-manifest.json", "/efi/unixnu/unixnu.toml"

def approved():
    recipe = json.loads((root/"release-source.json").read_bytes())
    assert recipe["version"] == 206 and recipe["parent_sha256"] == file_hash(parent_image) == base_sha
    assert recipe["units"] and recipe["changes"]
    for name, pair in recipe["units"].items():
        new = root/name; old = base_dir/name
        assert new.resolve().is_relative_to(root) and old.resolve().is_relative_to(base_dir), name
        assert (file_hash(old) if old.is_file() else None) == pair["parent"], name
        assert file_hash(new) == pair["candidate"], name
        assert pair["candidate"] != pair["parent"], name
    return recipe

def inputs():
    recipe = approved()
    parent_volume = volume(parent_image)
    old_menu = read(parent_volume,"TOML")
    cfg = {"title":"Thor v206 - native software display","replace_args":{},"append_args":[]}
    toml, args = p["pack"]["new_menu"](old_menu, cfg)
    old = json.loads(read(parent_volume,"MANIFEST"))
    assert args == old["boot_args"] and old["version"]==205
    settings = tomllib.loads(toml.decode())
    assert settings["boot"]=={"timeout":2,"default":"xnu"} and len(args.encode())==1020
    tag = (root/"runtime/ui-tag.txt").read_text().strip()
    runtime = json.loads((root/"runtime/final-inputs.json").read_bytes())
    manifest = dict(old, version=206, purpose=cfg["title"], release_variant="software-private-v206",
        candidate_parent=base_sha, candidate_change=recipe["changes"], kernel_source=str(kernel),
        kernel_sha256=file_hash(kernel), runtime_source="v206 release-source.json",
        config_change={"menu_reference":"v205 private","added":[]}, ui_stage_tag=tag,
        ui_sha256=runtime["payloads"]["libpong-ui.dylib"]["sha256"], 
        diagnostic_delta=recipe.get("diagnostic_delta","v205 app switch and scanout diagnostics retained"),
        hardware_validated=False, flash_performed=False, fastboot_boot_performed=False)
    return toml,dump(manifest),runtime,recipe

def verify(candidate):
    menu,manifest,runtime,recipe = inputs()
    old,firmware,signature,old_hash = decode(parent_image)
    new,fw,sig,h = decode(candidate)
    assert fw == firmware and sig == signature and old_hash == base_sha and old.keys()==new.keys()
    assert {n for n in old if old[n]!=new[n]}=={kname,mname,cname}
    K = new[kname]; assert K==kernel.read_bytes() and sha(K) != base_kernel_sha
    assert sha(old[kname])==base_kernel_sha and new[mname]==manifest and new[cname]==menu
    p["pack"]["MENU"]["verify_abi"]()
    arg = p["pack"]["MENU"]["validate_menu"](tomllib.loads(menu.decode()),json.loads(manifest))
    assert arg[0]["argument_bytes"]==1020
    old_runtime = json.loads((base_dir/"runtime/final-inputs.json").read_bytes())
    abi = p["pack"]["PREV"]["PREV"]["MACHO"]
    assert runtime["payloads"].keys() == old_runtime["payloads"].keys()
    for name,info in runtime["payloads"].items():
        path=root/"runtime"/name; data=path.read_bytes()
        assert sha(data)==info["sha256"] and len(data)==info["bytes"] and abi(data)["uuid"]==info["uuid"] and K.count(data)==1,name
        if info != old_runtime["payloads"][name]:
            assert (base_dir/"runtime"/name).read_bytes() not in K,name
        subprocess.run(["codesign","--verify","--strict",str(path)],check=True)
    tag=(root/"runtime/ui-tag.txt").read_text().strip()
    assert ("/private/var/pong/libpong-ui-"+tag+".dylib").encode()+b"\0" in K
    for token in ["THOR GPU V205: healthy kernel backend","THOR UI V205: software compositor selected","THOR RPMH V204:","THOR APP ROLE V205: at_us="] + recipe.get("kernel_markers",[]):
        assert token.encode() in K, token
    for token in recipe.get("new_kernel_markers",[]):
        assert token.encode() in K and token.encode() not in old[kname],token
    firmware_part,offset,length,_ = p["sections"](candidate)
    with tempfile.NamedTemporaryFile(dir=root,suffix=".fat") as f:
        with candidate.open("rb") as c: c.seek(offset); f.write(c.read(length))
        f.flush(); subprocess.run(["/sbin/fsck_msdos","-n",f.name],check=True,stdout=subprocess.DEVNULL)
    return dict(image=image.name, image_sha256=h, image_bytes=candidate.stat().st_size, mode="private", parent_sha256=base_sha, kernel_sha256=sha(K), filesystem_check=0, firmware_sha256=fw, signature_sha256=sig,runtime=runtime,ui_stage_tag=tag,boot_args=json.loads(manifest)["boot_args"],argument_check=arg, source=recipe, validated_on_device=False,
                members={k:sha(v) for k,v in new.items()})

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["package","verify"]);A=parser.parse_args()
    if A.action=="verify" or image.is_file():
        info=verify(image); assert info==json.loads(record.read_bytes())
    else:
        menu,manifest,_,_=inputs();assert file_hash(p["FULL"])==p["PARENT_FULL_SHA"]
        with tempfile.TemporaryDirectory(prefix=".pack-",dir=root) as d:
            d=Path(d);full=d/"full.img";candidate=d/"candidate.img"
            shutil.copyfile(p["FULL"],full)
            (d/"menu").write_bytes(menu);(d/"manifest").write_bytes(manifest)
            for path,kind in [(kernel,"KERNEL"),(d/"menu","TOML"),(d/"manifest","MANIFEST")]:
                subprocess.run(["mcopy","-o","-i",volume(full),str(path),F[kind]],check=True)
            p["compact"](full,candidate)
            info=verify(candidate);(d/"record").write_bytes(dump(info));os.replace(d/"record",record);os.link(candidate,image)
    print(dump(info).decode(),end="")
if __name__=="__main__":main()
