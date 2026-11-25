#!/usr/bin/python3.13
"""Threads of the port's processes in the stackshot inside a watchdog report (.ips written by ReportCrash).

usage: stackshot_threads.py <report.ips> [--pid N | --name PATTERN]... [--kernel <unstripped kernel>] [--frames N]

The report's JSON body has "stackshot": {processByPid, binaryImages, memoryStatus}.  A thread's user
frames are [image index, offset]; kernel addresses (waitEvent, continuation, kernelFrames) are
0xfffffff000000000 + offset in image 0.  The kernel is symbolicated with the unstripped kernel of the
same build.  User frames are listed by image UUID and offset (the shared cache is not symbolicated).
"""
import argparse, bisect, json, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("report")
ap.add_argument("--pid", action="append", default=[])
ap.add_argument("--name", action="append", default=[])
ap.add_argument("--kernel")
ap.add_argument("--frames", type=int, default=12)
ap.add_argument("--all-waits", action="store_true")
a = ap.parse_args()

raw = open(a.report, "rb").read().decode("utf-8", "replace")
body = json.loads(raw.split("\n", 1)[1])
shot = body["stackshot"]
images = shot["binaryImages"]
table = []
if a.kernel:
    out = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", "-n", a.kernel], text=True, stderr=subprocess.DEVNULL)
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 3:
            try:
                table.append((int(p[0], 16), p[2]))
            except ValueError:
                pass
    table.sort()
addrs = [t[0] for t in table]

def ksym(value):
    if not isinstance(value, int) or not table or value < 0xfffffff000000000 or value >= 0xfffffff100000000:
        return "%x" % (value if isinstance(value, int) else 0)
    i = bisect.bisect_right(addrs, value) - 1
    if i < 0:
        return "%x" % value
    return "%s+%#x" % (table[i][1], value - table[i][0])

def kaddr(frame):
    # [kind, value]: the kernel address is 0xfffffff000000000 + the low 36 bits of the value
    if isinstance(frame, list) and len(frame) == 2 and isinstance(frame[1], int):
        return 0xfffffff000000000 + (frame[1] & 0xFFFFFFFFF)
    return frame if isinstance(frame, int) else 0

wanted = set(int(p) for p in a.pid)
for pid, proc in sorted(shot["processByPid"].items(), key=lambda kv: int(kv[0])):
    name = proc.get("procname", "")
    if (wanted and int(pid) not in wanted) or (not wanted and not any(n.lower() in name.lower() for n in a.name)):
        continue
    print("===== pid %s %s  %s  user %.2fs system %.2fs  resident %d  suspendCount %s" % (
        pid, name, proc.get("rawFlags"), proc.get("userTimeTask", 0), proc.get("systemTimeTask", 0),
        proc.get("residentMemoryBytes", 0), proc.get("suspendCount")))
    for key in ("flags", "suspensionSources", "turnstileInfo", "waitInfo"):
        if proc.get(key):
            print("  %s: %s" % (key, json.dumps(proc[key])[:500]))
    for tid, t in sorted(proc["threadById"].items(), key=lambda kv: int(kv[0])):
        print("  -- thread %s %s prio %s/%s qos %s  user %.3fs system %.3fs  queue %r" % (
            tid, ",".join(t.get("state", [])), t.get("basePriority"), t.get("schedPriority"),
            t.get("qosRequested"), t.get("userTime", 0), t.get("systemTime", 0), t.get("dispatch_queue_label")))
        if t.get("snapshotFlags"):
            print("     flags %s" % ",".join(t.get("snapshotFlags")))
        if t.get("waitEvent") is not None:
            print("     waitEvent %s %#x %s" % (t["waitEvent"][0], kaddr(t["waitEvent"]), ksym(kaddr(t["waitEvent"]))))
        if t.get("continuation") is not None:
            print("     continuation %s" % ksym(kaddr(t["continuation"])))
        kf = t.get("kernelFrames")
        if kf:
            print("     kernel: %s" % " < ".join(ksym(kaddr(f)) for f in kf[:24]))
        frames = t.get("userFrames") or []
        if frames:
            print("     user: " + " < ".join("%s+%#x" % (images[f[0]][0][:8].upper(), f[1]) for f in frames[:a.frames]))
