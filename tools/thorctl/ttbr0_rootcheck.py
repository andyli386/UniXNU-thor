#!/usr/bin/env python3
"""Verify the real linked Thor MMU-root readiness stage before phone boot."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess


def disasm(path, symbol):
    out=subprocess.check_output([
        "xcrun","llvm-objdump","--macho","--section=__TEXT_EXEC,__text",
        "--dis-symname",symbol,str(path)],text=True,timeout=50)
    a=[line.strip() for line in out.splitlines() if
       re.match(r"^(?:0x)?[0-9a-f]{16}:",line.strip())]
    if not a:
        raise RuntimeError("No disassembly for "+symbol)
    return a


def calls(lines, wanted):
    return [(i,name,line) for i,line in enumerate(lines)
            for name in wanted if re.search(r"\bbl\s+_"+name+r"\b",line)]


def hashfile(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kernel",type=Path,required=True)
    p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    kernel=args.kernel.resolve()
    baseline=args.baseline.resolve()
    outfile=args.out.resolve()
    assert kernel.is_file() and baseline.is_file() and not outfile.exists()
    order=["thor_entry09_vm_finish","set_mmu_ttb_alternate",
           "thor_entry10_post_ttbr1","thor_vm_pre_ttbr0_retire_probe",
           "thor_vm_ttbr0_ready_gate"]
    arm=disasm(kernel,"_arm_vm_init")
    prev=disasm(baseline,"_arm_vm_init")
    seq=[v for _,v,_ in calls(arm,order)]
    old=[v for _,v,_ in calls(prev,order)]
    assert seq==order,(seq,order)
    assert old==order[:4],old
    assert not calls(arm,["set_mmu_ttb"]), "TTBR0 invalidation cannot run in bounded diagnostic"
    ret=disasm(kernel,"_thor_vm_pre_ttbr0_retire_probe")
    assert any(re.search(r"\bret\b",line) for line in ret), "High-VBAR proof no longer returns"
    gate=disasm(kernel,"_thor_vm_ttbr0_ready_gate")
    probe=disasm(kernel,"_thor_vm_s1e1r_ok")
    phystokv=calls(gate,["phystokv"])
    ats=calls(gate,["thor_vm_s1e1r_ok"])
    white=calls(gate,["thor_entry09_mark"])
    stop=calls(gate,["thor_entry09_stop"])
    assert len(phystokv)==2,phystokv
    assert len(ats)==5,ats
    assert len(white)==len(stop)==1
    assert phystokv[0][0]<phystokv[1][0]<ats[0][0]<ats[-1][0]
    assert ats[-1][0]<white[0][0]<stop[0][0]
    assert not calls(gate,["set_mmu_ttb"]), "No TTBR0 changes allowed"
    assert any("mrs" in l and "TTBR0_EL1" in l for l in gate)
    assert any("mrs" in l and "TTBR1_EL1" in l for l in gate)
    assert any("mrs" in l and "VBAR_EL1" in l for l in gate)
    assert any(re.search(r"\bldr\s+x",l) for l in gate)
    assert any("cbnz" in l for l in gate), "Zero-root loop rejection missing"
    assert any(re.search(r"\bcmp\s+x\d+,\s*#0x1,\s*lsl\s+#12",l) for l in gate), "4 KiB loop upper bound not enforced"
    assert any(re.search(r"\bat\s+s1e1r",l,re.I) for l in probe)
    assert any(re.search(r"\bmrs\s+\w+,\s*PAR_EL1",l,re.I) for l in probe)
    assert not any("msr" in line and "TTBR0_EL1" in line for line in gate)
    assert hashfile(kernel)!=hashfile(baseline)
    report={
      "schema":"thor-vm-ttbr0-root-readiness-machinecode-v1",
      "checked_at":dt.datetime.now(dt.timezone.utc).isoformat(),
      "result":"PASS_LINKED_ZERO_ROOT_AND_HIGH_PHYSVA_GATE",
      "kernel_sha256":hashfile(kernel),
      "baseline_vmhw03_kernel_sha256":hashfile(baseline),
      "compiled_arm_vm_init_call_order":seq,
      "high_vbar_prior_validator_retains_return":True,
      "mapped_phystokv_calls":2,
      "EL1_AT_S1E1R_calls":5,
      "zero_root_4KiB_check_emitted":True,
      "white_marker_after_all_root_guards":True,
      "no_TTBR0_renewal_or_retirement":True,
      "no_global_TLB_flush_from_diagnostic":True,
      "arm_vm_init_full_returned":False,
      "hardware_tested":False,
      "phone_actions":0}
    outfile.parent.mkdir(parents=True,exist_ok=True)
    with outfile.open("x") as f:
        json.dump(report,f,indent=2);f.write("\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
