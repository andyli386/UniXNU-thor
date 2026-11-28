#!/usr/bin/env python3.13
"""Threads that are stuck in the kernel in the stackshot inside a watchdog report (.ips).

  stackshot_stuck.py <report.ips> --kernel <unstripped kernel of that version> [--unint] [--pid N ...]

For every thread: state, how long ago it last ran, what its wait event is (nearest kernel symbol),
its continuation, dispatch queue label and thread name.  Default: uninterruptible threads and threads
whose wait event lies in driver or IOKit code, plus every thread that has not run for 30 s or more.
"""
import argparse, bisect, json, subprocess, sys
ap = argparse.ArgumentParser()
ap.add_argument("report"); ap.add_argument("--kernel", required=True); ap.add_argument("--pid", action="append", default=[])
ap.add_argument("--min-idle", type=float, default=30.0); ap.add_argument("--all", action="store_true")
a = ap.parse_args()
raw = open(a.report, "rb").read().decode("utf-8", "replace")
body = json.loads(raw.split("\n", 1)[1]); shot = body["stackshot"]
table = []
for line in subprocess.check_output(["xcrun", "nm", "-arch", "arm64", "-n", a.kernel], text=True, stderr=subprocess.DEVNULL).splitlines():
    p = line.split()
    if len(p) >= 3:
        try: table.append((int(p[0], 16), p[2], p[1]))
        except ValueError: pass
table.sort(); addrs = [t[0] for t in table]
def sym(v):
    if not isinstance(v, int) or v < 0xfffffff000000000 or v >= 0xfffffff100000000: return None
    i = bisect.bisect_right(addrs, v) - 1
    return None if i < 0 else (table[i][1], v - table[i][0])
def ka(f):
    return 0xffffffe000000000 + (f[1] & 0xFFFFFFFFF) if isinstance(f, list) and len(f) == 2 and isinstance(f[1], int) else 0
uptime = body.get("uptime")
pids = set(int(p) for p in a.pid)
rows = []
for pid, proc in sorted(shot["processByPid"].items(), key=lambda kv: int(kv[0])):
    name = proc.get("procname") or "?"
    if pids and int(pid) not in pids: continue
    for tid, t in proc["threadById"].items():
        st = t.get("state", [])
        we = t.get("waitEvent"); wea = ka(we) if we else 0; ws = sym(wea)
        co = t.get("continuation"); cs = sym(ka(co)) if co else None
        idle = uptime - t.get("lastRunTime", uptime) if uptime is not None else 0
        driver = bool(ws and not ws[0].startswith(("_lck", "_hw_lck", "_ipc", "_semaphore", "_ulock", "_workq", "_thread", "_sched", "_ml_", "_machine", "_arm", "_timer", "_clock", "_vm", "_pmap", "_task", "_proc", "_syslog", "_sleep", "_rpc", "_dtrace", "_kpc", "_sha", "_chud", "_etap", "_bt", "_kdebug", "_kd", "_ks", "_mps", "_pthread", "_ev", "_act", "_bank", "_resolv", "_mach", "_bsd", "_kevent", "_select", "_ubc", "_vnode", "_vfs", "_nio", "_msleep", "_io", "_waitq", "_turn", "_coal", "_ledger", "_energy", "_cpu", "_fi", "_fp", "_ne", "_io", "_dlil", "_ifnet", "_sock", "_mbuf", "_net", "_flow", "_necp", "_sky", "_ct", "_nstat", "_ip", "_tcp", "_udp", "_dl", "_lck"))) 
        unint = "TH_UNINT" in st
        if a.all or unint or driver or (idle >= a.min_idle and "TH_WAIT" in st):
            rows.append((name, int(pid), int(tid), st, idle, ws, cs, t.get("dispatch_queue_label"), t.get("name"), t.get("snapshotFlags"), t.get("systemTime"), t.get("userTime")))
rows.sort(key=lambda r: (-(r[4] or 0), r[0]))
for r in rows:
    st = ",".join(s for s in r[3] if s not in ("TH_WAIT",))
    print("%-22s pid %-4d tid %-5d %-18s idle %7.1fs wait %-34s cont %-30s q=%s name=%s sys=%.2f usr=%.2f %s" % (
        r[0], r[1], r[2], st or "WAIT", r[4] or 0,
        ("%s+%#x" % r[5]) if r[5] else ("-" if not r[5] else ""), ("%s+%#x" % r[6]) if r[6] else "-",
        r[7], r[8], r[10] or 0, r[11] or 0, ",".join(f for f in (r[9] or []) if f not in ("kUser64_p",))))
print("rows: %d of %d threads" % (len(rows), sum(len(p["threadById"]) for p in shot["processByPid"].values())))
