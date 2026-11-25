"""Split a kernel-console capture into boots and summarise them.

  console_sessions.py <console.txt> [--tail N] [--boot UUID8] [--grep RE] [--around RE] [--context N]

Sessions start at "Pong console capture BEGIN".  For each session: boot UUID, first and last kernel
timestamp, line count, and how many NUL bytes the file tail carries (what an unflushed APFS tail reads
back as after a forced reboot).  --tail prints the last N lines of the chosen boot, --around prints
the lines around every match of a regular expression.
"""
import argparse
import re
import pathlib
import sys

MARK = re.compile(rb"^Pong console capture BEGIN boot=([0-9A-Fa-f-]+) pid=(\d+)", re.M)
STAMP = re.compile(rb"^\[\s*(\d+\.\d+)\]")


def sessions(data):
    starts = [m.start() for m in MARK.finditer(data)]
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(data)
        block = data[start:end]
        boot = MARK.match(block).group(1).decode().upper()
        lines = block.split(b"\n")
        stamps = [float(m.group(1)) for l in lines for m in [STAMP.match(l)] if m]
        nul = len(block) - len(block.rstrip(b"\x00")) if block.rstrip(b"\x00") != block else 0
        # Everything after the last stamped kernel line is what the forced reboot left unreadable.
        last = max((i for i, l in enumerate(lines) if STAMP.match(l)), default=0)
        after = b"\n".join(lines[last + 1:])
        yield dict(boot=boot, pid=int(MARK.match(block).group(2)), lines=lines, bytes=len(block),
                   first=stamps[0] if stamps else None, last=stamps[-1] if stamps else None,
                   stamps=len(stamps), nul_tail=nul, after_lines=len(lines) - last - 1, after_bytes=len(after))


def show(lines, stamp0=None):
    out = []
    for l in lines:
        l = l.rstrip(b"\r")
        if stamp0 is not None:
            m = STAMP.match(l)
            if m:
                out.append("[%9.3f] %s" % (float(m.group(1)) - stamp0, l[m.end():].decode("utf-8", "replace")))
                continue
        out.append(l.decode("utf-8", "replace"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--tail", type=int, default=0)
    ap.add_argument("--boot")
    ap.add_argument("--grep")
    ap.add_argument("--around")
    ap.add_argument("--context", type=int, default=6)
    ap.add_argument("--width", type=int, default=260)
    ap.add_argument("--exclude", help="drop lines matching this regular expression")
    ap.add_argument("--from-time", type=float, help="window start, seconds from the boot's first stamp")
    ap.add_argument("--to-time", type=float, help="window end, seconds from the boot's first stamp")
    ap.add_argument("--every", type=int, default=1, help="print only every Nth line of a run")
    a = ap.parse_args()
    data = pathlib.Path(a.file).read_bytes()
    all_sessions = list(sessions(data))
    for i, s in enumerate(all_sessions):
        print("%2d boot=%s pid=%d lines=%d stamped=%d kernel=%s..%s seconds=%s bytes=%d unreadable tail: lines=%d bytes=%d (NUL %d)%s" % (
            i, s["boot"][:8], s["pid"], len(s["lines"]), s["stamps"], s["first"], s["last"],
            ("%.1f" % (s["last"] - s["first"])) if s["first"] is not None and s["last"] is not None else "?",
            s["bytes"], s["after_lines"], s["after_bytes"], s["nul_tail"],
            "  <-- chosen" if a.boot and s["boot"].startswith(a.boot.upper()) else ""))
    chosen = [s for s in all_sessions if not a.boot or s["boot"].startswith(a.boot.upper())]
    if not chosen:
        sys.exit("no boot matches %r" % a.boot)
    s = chosen[-1]
    drop = re.compile(a.exclude.encode()) if a.exclude else None

    def stamp_of(line):
        m = STAMP.match(line)
        if not m or s["first"] is None:
            return None
        return float(m.group(1)) - s["first"]

    keep = []
    running = None
    for i, l in enumerate(s["lines"]):
        t = stamp_of(l)
        if t is not None:
            running = t                     # a line without a stamp belongs to the moment of the last stamped one
        t = running
        if drop and drop.search(l):
            continue
        if t is not None and ((a.from_time is not None and t < a.from_time) or (a.to_time is not None and t > a.to_time)):
            continue
        keep.append(i)
    if a.tail:
        s["lines"] = [s["lines"][i] for i in keep]
        clean = [l for l in s["lines"] if b"\x00" not in l]
    else:
        clean = [l for l in s["lines"] if b"\x00" not in l]
    if a.tail:
        print("\n=== last %d lines of boot %s (time from the boot's first stamp)" % (a.tail, s["boot"][:8]))
        for line in show(clean[-a.tail:], s["first"]):
            print(line[:a.width])
    if not a.grep and not a.around and (a.exclude or a.from_time is not None or a.to_time is not None):
        print("\n=== %d lines kept of boot %s%s" % (len(keep), s["boot"][:8], " (every %dth shown)" % a.every if a.every > 1 else ""))
        for n, i in enumerate(keep):
            if n % a.every:
                continue
            for line in show([s["lines"][i]], s["first"]):
                print(line[:a.width])
    for pattern in (a.grep, a.around):
        if not pattern:
            continue
        rx = re.compile(pattern.encode())
        hits = [i for i, l in enumerate(s["lines"]) if rx.search(l)]
        print("\n=== %d lines matching /%s/ in boot %s" % (len(hits), pattern, s["boot"][:8]))
        wanted = set()
        for i in hits:
            if a.around:
                wanted.update(range(max(0, i - a.context), min(len(s["lines"]), i + a.context + 1)))
            else:
                wanted.add(i)
        last = None
        for i in sorted(wanted):
            if last is not None and i != last + 1:
                print("    ...")
            for line in show([s["lines"][i]], s["first"]):
                print(line[:a.width])
            last = i


if __name__ == "__main__":
    main()
