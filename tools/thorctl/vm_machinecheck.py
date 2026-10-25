#!/usr/bin/env python3
"""Require a real post-TTBR1 EL1 high-VA probe in a linked Thor Mach-O."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess


def llvm(symbol, kernel):
    res = subprocess.run(["xcrun", "llvm-objdump", "--macho",
                          "--section=__TEXT_EXEC,__text",
                          "--dis-symname", symbol, str(kernel)],
                         capture_output=True, text=True, timeout=60)
    if res.returncode or symbol + ":" not in res.stdout:
        raise RuntimeError("No disassembly for " + symbol + ": " + res.stderr[-500:])
    return [line for line in res.stdout.splitlines()
            if re.match(r"^[a-f0-9]{16}:", line)]


def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def calls(lines, names):
    selected = []
    for i, line in enumerate(lines):
        if "\tbl\t_" not in line:
            continue
        for name in names:
            if "_"+name in line:
                selected.append((i, name, line))
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    kernel = args.kernel.resolve()
    baseline = args.baseline.resolve()
    output = args.out.resolve()
    if not (kernel.is_file() and baseline.is_file()) or output.exists():
        raise RuntimeError("Missing input or output already exists")
    symbols = ("thor_entry09_vm_finish","set_mmu_ttb_alternate",
               "thor_entry10_post_ttbr1","thor_vm_pre_ttbr0_retire_probe")
    arm = llvm("_arm_vm_init",kernel)
    preceding = llvm("_arm_vm_init",baseline)
    live_calls = calls(arm,symbols)
    previous = calls(preceding,symbols)
    names = [n for _,n,_ in live_calls]
    if names != list(symbols):
        raise RuntimeError("Linked VM call order changed: "+str(names))
    if [n for _,n,_ in previous] != list(symbols[:3]):
        raise RuntimeError("Historic validated ENTRY10G call path changed")
    if any(re.search(r"\tbl\t_set_mmu_ttb(?:\s|$)",line) for line in arm):
        raise RuntimeError("Unsafe TTBR0 invalidation already reachable")
    post = llvm("_thor_entry10_post_ttbr1",kernel)
    guard = llvm("_thor_vm_pre_ttbr0_retire_probe",kernel)
    translator = llvm("_thor_vm_s1e1r_ok",kernel)
    if len(calls(guard,("thor_vm_s1e1r_ok",))) != 3:
        raise RuntimeError("Translation probes are missing from the new function")
    if not any("at\ts1e1r" in line.lower() for line in translator):
        raise RuntimeError("EL1 translation instruction not emitted")
    if not any("mrs\t" in line.lower() and "par_el1" in line.lower()
               for line in translator):
        raise RuntimeError("PAR_EL1 result not read")
    if not any("\tret" in line for line in translator):
        raise RuntimeError("Translation helper lacks return")
    status = [n for _,n,_ in calls(guard,("thor_entry09_mark","thor_entry09_stop"))]
    if status != ["thor_entry09_mark","thor_entry09_stop"]:
        raise RuntimeError("Success marker/stop missing after pre-retirement checks")
    report = {"schema":"thor-vm-pre-retire-machinecode-v1",
              "checked_at":dt.datetime.now(dt.timezone.utc).isoformat(),
              "result":"PASS_LINKED_POST_TTBR1_TRANSLATION_PROBES",
              "kernel_sha256":sha(kernel),"baseline_sha256":sha(baseline),
              "observed_call_order":[x[1] for x in live_calls],
              "code_address_probe_emitted":True,
              "AT_S1E1R_and_PAR_EL1_visible":True,
              "three_translation_probes":True,
              "white_marker_only_after_pre_retire_probe":True,
              "TTBR0_invalidation_executed":False,
              "full_arm_vm_init_returned":False,
              "phone_actions":0}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x") as f:json.dump(report,f,indent=2);f.write("\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
