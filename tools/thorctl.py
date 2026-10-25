#!/usr/bin/env python3
"""Thor CI control plane (P0). No hidden device actions or mutable global status."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

RUN_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{5,96}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")


def die(message):
    raise RuntimeError(message)


def hashfile(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def path_under(root, rel, exists=True):
    root = root.resolve()
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        die("Path escapes workspace: " + str(rel))
    if exists and not p.exists():
        die("Missing required path: " + str(p))
    return p


def read_json(p):
    return json.loads(Path(p).read_text())


def config_at(root, location):
    c = tomllib.loads(path_under(root, location).read_text())
    assert c["schema_version"] == 1 and c["target"]["board"] == "thor"
    assert c["target"]["profile"] == "vm-post-ttbr1"
    assert c["test"]["temporary_boot_only"] is True
    assert c["test"]["max_boot_attempts"] == 1
    for key in ("kernel", "firmware", "stock", "paths", "test"):
        assert key in c
    for key in ("bootshim_sha256", "dtb_sha256", "verified_entry09_kernel_sha256"):
        assert HEX64.fullmatch(c["stock"][key])
    return c


def git_at(repo, ref):
    def git(*args):
        cp = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True, timeout=20)
        if cp.returncode:
            die("Git " + " ".join(args) + ": " + cp.stderr[-160:])
        return cp.stdout.strip()
    commit = git("rev-parse", ref + "^{commit}")
    assert re.fullmatch("[a-f0-9]{40}", commit)
    return {"commit": commit, "ref": ref, "path": str(repo),
            "current_head": git("rev-parse", "HEAD"),
            "working_tree_dirty": bool(git("status", "--porcelain"))}


def host_files(root, config, host):
    if host not in config["paths"]:
        die("Unknown host: " + host)
    return {k: path_under(root, v) for k, v in config["paths"][host].items()}


def build_lock(root, config, host):
    p = host_files(root, config, host)
    real_hash = hashfile(p["kernel"])
    layout = read_json(p["layout"])
    release = read_json(p["kernel_release"])
    if real_hash != layout["kernel_sha256"] or real_hash != release["kernel_sha256"]:
        die("Frozen kernel, Mach-O geometry and source release disagree")
    if p["kernel"].stat().st_size != layout["kernel_bytes"]:
        die("Mach-O byte size changed")
    if release["loaded_span_sha256"] != layout["loaded_span_sha256"]:
        die("Mapped span identity changed")
    firmware = git_at(p["firmware_source"], config["firmware"]["ref_" + host])
    fd_receipt = read_json(p["firmware_release"])
    if firmware["commit"] != fd_receipt["uefi_commit"]:
        die("UEFI commit differs from its frozen firmware release")
    if host == "macmini":
        xnu = git_at(p["kernel_source"], config["kernel"]["ref"])
        if xnu["commit"] != release["xnu_commit"]:
            die("Source revision does not match linked XNU kernel")
    else:
        xnu = {"commit": release["xnu_commit"],
               "verification": "frozen kernel receipt; source compiled on Mac"}
    data = {"schema": "thor-build-lock-v1",
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "board": "thor", "profile": config["target"]["profile"], "host": host,
            "kernel": {"sha256": real_hash, "bytes": p["kernel"].stat().st_size,
                       "layout_sha256": hashfile(p["layout"]),
                       "build_receipt_sha256": hashfile(p["kernel_release"]),
                       "entry_offset": layout["entry_offset"],
                       "vectors_offset": layout["vectors_offset"],
                       "loaded_span_bytes": layout["loaded_span_bytes"],
                       "loaded_span_sha256": layout["loaded_span_sha256"]},
            "xnu_source": xnu, "uefi_source": firmware,
            "verified_entry09_kernel_sha256": config["stock"]["verified_entry09_kernel_sha256"],
            "phone_actions": 0}
    if "fd" in p:
        data["uefi_fd"] = {"sha256": hashfile(p["fd"]),
                           "bytes": p["fd"].stat().st_size}
    return data


def new_file(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def run_root(root, run_id):
    if not RUN_ID.fullmatch(run_id):
        die("Invalid run ID")
    return path_under(root, "runs/" + run_id, exists=False)


def affected(files):
    checks = {"L0:format-diff-configuration"}
    cause = []
    for name in files:
        s = name.lower()
        if any(x in s for x in ("vm_init", "ttbr", "mmu", "page_table", "start.s")):
            checks.update(("L1:vm-boundaries", "L2:xnu-link",
                           "L2:machine-code-controlflow", "L2:kernel-uefi-abi",
                           "L2:boot-image"))
            cause.append("VM/translation path")
        elif "uefi" in s or "ebs" in s or "thorentrybds" in s:
            checks.update(("L1:uefi-module", "L2:uefi-link", "L2:fv-ffs-audit",
                           "L2:boot-image"))
            cause.append("UEFI/EBS")
        elif "adt" in s or s.endswith((".dts", ".dtb")):
            checks.update(("L1:adt-layout", "L2:handoff-contract", "L2:boot-image"))
            cause.append("ADT")
        elif "driver" in s or "iokit" in s:
            checks.update(("L1:driver-api", "L2:xnu-link", "L2:kernel-abi"))
            cause.append("driver")
        elif s.endswith((".py", ".toml")):
            checks.add("L1:host-unit")
            cause.append("host tools")
        else:
            checks.add("L0:documentation-or-local-change")
    if not files:
        checks.add("L1:host-unit")
    return {"schema": "thor-affected-checks-v1", "files": files,
            "checks": sorted(checks), "reasons": sorted(set(cause)),
            "hardware_never_automatic": True,
            "full_candidate_gate_required": any(x.startswith("L2:") for x in checks)}


def package_status(root, config, host):
    if host != "i386":
        die("Only i386 holds actual boot images for this phase")
    p = host_files(root, config, host)
    freeze = build_lock(root, config, host)
    meta = read_json(p["image_manifest"])
    image_sha = hashfile(p["image"])
    if image_sha != meta["image_sha256"] or p["image"].stat().st_size != meta["image_bytes"]:
        die("Boot image digest or length not bound to actual artifact")
    if meta["kernel_sha256"] != freeze["kernel"]["sha256"]:
        die("Boot image kernel mismatch")
    if meta["fd_sha256"] != freeze["uefi_fd"]["sha256"]:
        die("Boot image firmware mismatch")
    if meta["uefi_commit"] != freeze["uefi_source"]["commit"]:
        die("UEFI source mismatch")
    if not HEX64.fullmatch(meta["seed_sha256"]):
        die("No recorded one-time entropy identity")
    reasons = [n for n in ("INVALID-NOT-FOR-PHONE",
                            "ONE_SHOT_USED-DO-NOT-BOOT-AGAIN") if (p["image"].parent/n).exists()]
    return {"schema": "thor-package-verification-v1", "image_sha256": image_sha,
            "bytes": p["image"].stat().st_size, "kernel_sha256": meta["kernel_sha256"],
            "uefi_fd_sha256": meta["fd_sha256"],
            "refuse_boot_markers": reasons,
            "eligible_for_first_boot": not bool(reasons),
            "phone_actions": 0}


def verify_run(root, run_id):
    run_root(root, run_id)
    options = [root/"runs"/run_id/"l3", root/"evidence/hardware-tests"/run_id,
               root/"analysis/hardware-tests"/run_id]
    candidates = [d for d in options if d.is_dir()]
    if not candidates:
        die("No hardware evidence directory for run ID")
    d = candidates[0]
    found = sorted(d.glob("technical-summary*.json"))
    if len(found) != 1:
        die("Expected exactly one hardware technical summary")
    summary = read_json(found[0])
    for name, expected in (summary.get("raw_evidence_sha256") or
                           summary.get("hardware_evidence_sha256") or
                           summary.get("evidence_sha256") or {}).items():
        if hashfile(d/name) != expected:
            die("Altered hardware evidence " + name)
    photo = d/"visual-observation.json"
    visual = read_json(photo) if photo.is_file() else None
    reviewed_file = d/"visual-milestone-review.json"
    if visual is None and reviewed_file.is_file():
        entry = read_json(reviewed_file)
        photos = entry.get("photos", [])
        if (entry.get("run_id") == run_id and len(photos) >= 2
            and len({f.get("sha256") for f in photos}) == len(photos)
            and all(HEX64.fullmatch(f.get("sha256", "")) for f in photos)):
            visual = entry
    accepted = bool(summary.get("temporary_fastboot_boot_accepted") or
                    summary.get("fastboot_boot_accepted"))
    android = bool(summary.get("android_return_passed"))
    older_review = bool(visual and visual.get("review") == "independent"
                        and visual.get("post_ttbr1_state") == "green-cyan-white"
                        and visual.get("source_photo_sha256"))
    milestone_review = bool(
        visual and visual.get("conclusion") ==
        "PASS_BOUNDED_REAL_XNU_TTBR1_AND_HIGH_VBAR_CHECKPOINT_VISUALLY_CONFIRMED"
        and visual.get("code_path_basis", {}).get("image_sha256") == summary.get("image_sha256")
        and visual.get("code_path_basis", {}).get("xnu_commit") == summary.get("xnu_commit")
        and visual.get("observed", {}).get("xnu_white_high_vbar_pass") is True)
    validated = bool(accepted and android and (older_review or milestone_review))
    return {"schema": "thor-evidence-derived-status-v1", "run_id": run_id,
            "fastboot_command_accepted": accepted,
            "android_return_verified": android,
            "source_has_compiled_ttbr1_call": bool(
                summary.get("preboot_mach_o_call_path_proven") or
                (visual and visual.get("code_path_basis", {}).get("xnu_commit") ==
                 summary.get("xnu_commit") and
                 "set_mmu_ttb_alternate" in
                 visual.get("code_path_basis", {}).get("compiled_call_order", []))),
            "visual_checkpoint": visual,
            "post_ttbr1_validated_by_visual_record": validated,
            "raw_photo_bytes_sha256_verified": bool(
                visual and visual.get("photo_bytes_available_to_remote_host") and
                visual.get("raw_originals_sha256_validated")),
            "full_arm_vm_init_verified": False,
            "no_implicit_full_boot_claim": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", type=Path, default=Path.cwd())
    ap.add_argument("--config", default="thor-build.toml")
    cmd = ap.add_subparsers(dest="cmd", required=True)
    p = cmd.add_parser("check")
    p.add_argument("--host", choices=("macmini", "i386"), required=True)
    p = cmd.add_parser("inspect")
    p.add_argument("--host", choices=("macmini", "i386"), required=True)
    p.add_argument("--emit-run")
    p = cmd.add_parser("test")
    p.add_argument("--affected", nargs="*", default=[])
    p.add_argument("--execute", action="store_true")
    p = cmd.add_parser("build")
    p.add_argument("--host", choices=("macmini","i386"), required=True)
    p.add_argument("--run-id",required=True)
    p.add_argument("--stage",choices=("link","audit"))
    p.add_argument("--dry-run",action="store_true")
    p = cmd.add_parser("audit")
    p.add_argument("--host",choices=("macmini","i386"),required=True)
    p.add_argument("--run-id",required=True)
    p.add_argument("--dry-run",action="store_true")
    p = cmd.add_parser("package")
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--verify", action="store_true")
    group.add_argument("--create", action="store_true")
    p.add_argument("--host", choices=("macmini", "i386"), required=True)
    p.add_argument("--run-id")
    p.add_argument("--audit-run-id")
    p.add_argument("--dry-run",action="store_true")
    p = cmd.add_parser("device")
    p.add_argument("action", choices=("prepare", "boot"))
    p.add_argument("--host", choices=("macmini", "i386"), required=True)
    p.add_argument("--confirm-run-id")
    p = cmd.add_parser("run")
    p.add_argument("action", choices=("verify", "report"))
    p.add_argument("--run-id", required=True)
    args = ap.parse_args()
    root = args.workspace.resolve()
    cfg = config_at(root, args.config)
    if args.cmd == "check":
        result = {"result": "PASS", "contract": build_lock(root, cfg, args.host)}
    elif args.cmd == "inspect":
        result = build_lock(root, cfg, args.host)
        if args.emit_run:
            saved = new_file(run_root(root,args.emit_run)/"build.lock.json", result)
            result = {"result": "NEW_IMMUTABLE_LOCK", "path": str(saved),
                      "sha256": hashfile(saved)}
    elif args.cmd == "test":
        result = affected(args.affected)
        if args.execute:
            command = [str(root/"toolchains/python313/bin/python3"),"-m","unittest",
                       "discover","-s",str(root/"repos/UniXNU-thor/tests"),
                       "-p","test_thorctl_workflow.py","-q"]
            if not (root/"toolchains/python313/bin/python3").exists():
                die("Unit test runner only available on Mac mini")
            result["test_exit_code"] = subprocess.run(command,timeout=90).returncode
            if result["test_exit_code"] != 0: die("Unit test suite failed")
    elif args.cmd in ("build","audit") or (args.cmd=="package" and args.create):
        if not args.run_id or not args.run_id.startswith("thor-"):
            die("New build/package run IDs must begin with thor-")
        rpath=run_root(root,args.run_id)
        if args.cmd == "build":
            ps=host_files(root,cfg,args.host)
            if args.host=="macmini":
                driver=ps["vm_build_driver"]
                if not args.stage:die("Mac XNU build requires --stage link or audit")
                argv=[str(root/"toolchains/python313/bin/python3"),str(driver),
                      args.stage,"--run-id",args.run_id]
            else:
                driver=root/"tools/thorctl/uefi_build.py"
                argv=[sys.executable,str(driver),"--root",str(root),
                      "--source",str(ps["firmware_source"]),
                      "--run-id",args.run_id,
                      "--build-id",cfg["firmware"]["build_id"]]
                if args.dry_run:argv.append("--dry-run")
        elif args.cmd=="audit":
            if args.host != "i386":die("UEFI composition auditing still runs on i386")
            ps=host_files(root,cfg,args.host)
            output=rpath/"l1"/"uefi-audit"
            argv=[sys.executable,str(root/"tools/thorctl/firmware_audit.py"),
                  "--root",str(root),"--source",str(ps["firmware_source"]),
                  "--out",str(output)]
        else:
            if args.host!="i386":die("One-use Android Boot packaging still runs on i386")
            if not args.audit_run_id:die("Package creation requires --audit-run-id")
            ps=host_files(root,cfg,args.host)
            audit_root=run_root(root,args.audit_run_id)
            proofs=[p for suffix in ("l1/uefi-audit","l2/uefi-audit")
                    for p in (audit_root/suffix).glob("thor_fd_*-audit.json")]
            if len(proofs)!=1:die("Expected exactly one audited FD receipt")
            component=build_lock(root,cfg,args.host)
            if read_json(proofs[0])["fd"]["sha256"]!=component["uefi_fd"]["sha256"]:
                die("Requested firmware audit belongs to a different FD")
            provdir=ps["firmware_source"]/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
            matches=[x for x in provdir.glob("ENTRY*_PROVENANCE.json")
                if read_json(x).get("kernel_sha256")==component["kernel"]["sha256"]]
            if len(matches)!=1:die("Cannot uniquely derive matching kernel provenance")
            driver=root/"tools/thorctl/package.py"
            argv=[sys.executable,str(driver),"--root",str(root),
                  "--source",str(ps["firmware_source"]),
                  "--kernel",str(ps["kernel"]),"--fd",str(ps["fd"]),
                  "--fd-audit",str(proofs[0]),
                  "--firmware-receipt",str(ps["firmware_release"]),
                  "--provenance",str(matches[0]),"--run-id",args.run_id]
            if args.dry_run:argv.append("--dry-run")
        if args.cmd=="audit" and args.dry_run:
            result={"dry_run":True,"argv":argv,"phone_actions":0}
        elif args.cmd=="build" and args.host=="macmini" and args.dry_run:
            result={"dry_run":True,"argv":argv,"phone_actions":0}
        else:
            rc=subprocess.run(argv,timeout=2000).returncode
            if rc!=0:die("Stage failed (exit "+str(rc)+")")
            result={"result":"STAGE_PASSED","argv":argv,"phone_actions":0}
    elif args.cmd == "package":
        result = package_status(root, cfg, args.host)
    elif args.cmd == "device" and args.action == "prepare":
        pkg = package_status(root,cfg,args.host)
        if not pkg["eligible_for_first_boot"]:
            die("One-use image already consumed or invalid; device not touched")
        result = {"result":"HOST_PREPARED_NO_PHONE_ACTIONS","package":pkg}
    elif args.cmd == "device":
        die("P0 device boot is deliberately locked; require a reviewed per-run controller and explicit user authorization")
    else:
        data = verify_run(root,args.run_id)
        result = data if args.action == "verify" else {"report":data,
                "next":"Independently review visual checkpoint; retain kernel/UEFI/Android evidence separately"}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError,ValueError,KeyError,FileNotFoundError,AssertionError) as error:
        print("thorctl: "+str(error),file=sys.stderr)
        raise SystemExit(2)
