"""Stage the read-only capture and validators, including both generations of diagnostic files."""
from pathlib import Path
import shutil
import subprocess
import sys

work = Path(__file__).resolve().parent
previous = work.parent / "v219-history-capture-work"
dest = Path(sys.argv[1]).resolve()
subprocess.run([sys.executable, "-IB", str(previous / "prepare.py"), str(dest)], check=True)
(dest / "history.py").rename(dest / "legacy_history.py")
shutil.copy2(work / "history.py", dest / "history.py")

def revise(name, pairs):
    path = dest / name
    source = path.read_text()
    for old, new in pairs:
        if source.count(old) != 1:
            raise ValueError(f"{path.name}: expected one match for {old[:80]!r}")
        source = source.replace(old, new)
    path.write_text(source)

def add_paths(names):
    return (f'{names} += [s.replace("/ThorStall-", "/ThorStall-v220-").replace('
            f'"/ThorSwipeStack-", "/ThorSwipeStack-v220-") for s in {names}]')

revise("capture-reconnect.py", [
    ("        freeze_files = [", """        # Settings' durable return journal has its own single fixed path and size limit.
        manifest['volumes'][region]['files'] += probe(container, volume,
            ['mobile/Library/Logs/ThorSettingsReturn-v225.bin'], 'Data',
            lambda name, ino: ino.is_reg and ino.size <= 65536)
        freeze_files = ["""),
    ("        manifest['volumes'][region]['freeze_schema'] = 210",
     "        " + add_paths("freeze_files") + "\n        manifest['volumes'][region]['freeze_schema'] = 220"),
    ("set(session_info['candidates']) | set(session_info['trace_only_candidates'])",
     "set(session_info['corroborated_v227'][-2:]) | set(session_info['trace_only_candidates'])")
])
revise("read-stall-events.py", [
    ("\n\ndef read_events(", "\n" + add_paths("RECOVERY_PATHS") + "\n\ndef read_events("),
    ('role = path.stem.removeprefix("ThorStall-")', 'role = path.stem.removeprefix("ThorStall-").removeprefix("v220-")')
])
revise("freeze-evidence.py", [
    ("PATHS = journal.RECOVERY_PATHS + STACK_PATHS", add_paths("STACK_PATHS") + "\nPATHS = journal.RECOVERY_PATHS + STACK_PATHS"),
    ('    kind, slot = filename.removesuffix(".txt").rsplit("-", 1)',
     '    filename = filename.replace("ThorSwipeStack-v220-", "ThorSwipeStack-", 1)\n    kind, slot = filename.removesuffix(".txt").rsplit("-", 1)'),
    ("208:14,210:20}", "208:14,210:20,220:20}"),
    ("expected=[p for p in PATHS if p in journal.RECOVERY_PATHS or int(Path(p).stem.rsplit(\"-\",1)[1])<slots]",
     'expected = [p for p in PATHS if (schema == 220 or "-v220-" not in p) and\n                (p in journal.RECOVERY_PATHS or int(Path(p).stem.rsplit("-",1)[1]) < slots)]'),
    ('role = p.stem.removeprefix("ThorStall-")', 'role = p.stem.removeprefix("ThorStall-").removeprefix("v220-")')
])

import hashlib
reader_baseline_sha = 'cf94c817b66c47156116e4d8a5149c3e9295943b9221c8f27a6c69e1b4ff3c58'
reader = dest / "read-stall-events.py"
if hashlib.sha256(reader.read_bytes()).hexdigest() != reader_baseline_sha:
    raise ValueError("unexpected recovery event-reader baseline")
shutil.copy2(work / "read-stall-events.py", reader)
