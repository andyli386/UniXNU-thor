"""Annotate a THOR WITNESS V233 report with kernel symbols.

  witness_report.py <mirror-file> [--kernel <mach.development.pong.unstripped>] [--report N|all]

The mirror file written by the kernel (/private/var/logs/PongConsole-kernel-v233.txt) is the
kernel console followed by one block per hang report ("===== THOR WITNESS V233 REPORT n =====").
In every report line:
  k=<addr>,...   return addresses on a blocked thread's kernel stack   -> symbol+offset
  we=<addr>      the event the thread waits on (a kernel address)      -> nearest symbol+offset
  u=<pc>,<lr>    user registers (not symbolicated: per-process slide needed)
The kernel slide is taken from the report's own reference line: the runtime address of
panic_spin_forever minus its link-time address in the unstripped kernel.
"""
import argparse
import bisect
import pathlib
import re
import subprocess

FRAME = re.compile(r"\bk=([0-9a-f,]+)")
EVENT = re.compile(r"\bwe=([0-9a-f]+)")
REFERENCE = re.compile(r"panic_spin_forever at ([0-9a-f]+)")
HEADER = re.compile(r"^===== THOR WITNESS V233 REPORT (\d+)", re.M)


def symbols(kernel):
    out = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", "-n", str(kernel)], text=True, stderr=subprocess.DEVNULL)
    table = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in "TtSsDdBb" and not parts[2].startswith("$"):
            table.append((int(parts[0], 16), parts[2]))
    table.sort()
    return [a for a, _ in table], [n for _, n in table]


class Names:
    def __init__(self, kernel):
        self.kernel = pathlib.Path(kernel) if kernel else None
        self.addresses, self.names = symbols(kernel) if kernel else ([], [])
        self.by_name = dict(zip(self.names, self.addresses))
        self.slide = None

    def link(self, name):
        return self.by_name.get(name)

    def at(self, value):
        if not value or not self.addresses or self.slide is None:
            return None
        i = bisect.bisect_right(self.addresses, value - self.slide) - 1
        if i < 0:
            return None
        address, name = self.addresses[i], self.names[i]
        return "%s+0x%x" % (name, value - self.slide - address)


def annotate(names, text):
    out = []
    for line in text.splitlines():
        m = REFERENCE.search(line)
        if m and names.kernel:
            link = names.link("panic_spin_forever") or names.link("_panic_spin_forever")
            if link is not None:
                names.slide = int(m.group(1), 16) - link
                line += "   [kernel slide %#x]" % names.slide
        event = EVENT.search(line)
        if event:
            named = names.at(int(event.group(1), 16))
            if named:
                line += "   [we: %s]" % named
        frames = FRAME.search(line)
        if frames:
            named = [names.at(int(f, 16)) or "?" for f in frames.group(1).split(",") if f]
            line += "\n      k: " + " <- ".join(named)
        out.append(line)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--kernel", default=None)
    ap.add_argument("--report", default="all", help="report number or all")
    ap.add_argument("--no-threads", action="store_true", help="leave the per-thread lines out")
    a = ap.parse_args()
    data = pathlib.Path(a.file).read_bytes().decode("utf-8", "replace")
    names = Names(a.kernel)
    blocks = [(m.group(1), m.start()) for m in HEADER.finditer(data)]
    if not blocks:
        print("no report in %s" % a.file)
        tail = data[-4000:]
        print("--- last 4000 characters of the console ---")
        print(annotate(names, tail))
        return
    for number, start in blocks:
        if a.report != "all" and a.report != number:
            continue
        print("=" * 100)
        print("REPORT", number)
        print("=" * 100)
        body = annotate(names, data[start:])
        for line in body.splitlines():
            if a.no_threads and re.match(r"^ t\d+ ", line):
                continue
            print(line)


if __name__ == "__main__":
    main()
