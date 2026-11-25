from pathlib import Path
import json
import runpy
import sys

work = Path(__file__).resolve().parent
capture = Path(sys.argv[1]).resolve()
manifest = json.loads((capture / "manifest.json").read_bytes())
if (capture / "capture.status").read_text().strip() != "0" or not manifest["verified_zero_writes"]:
    raise ValueError("Missing read-only capture receipt")
inventory = runpy.run_path(str(capture / "history.py"))["inventory"](capture, manifest["volumes"]["Data"]["files"])
(capture / "console-history.json").write_text(json.dumps(inventory, indent=2) + "\n")
clock = runpy.run_path(str(work.parent / "v206-cadence/console-clock.py"))
records = clock["common"]["records_for"](capture)
freeze = runpy.run_path(str(capture / "freeze-evidence.py"))["report"]
for boot in inventory["corroborated_v227"][-2:]:
    report = freeze(capture, boot)
    folder = capture / ("v227-" + boot)
    folder.mkdir(exist_ok=True)
    roles = runpy.run_path(str(capture / "read-stall-events.py"))["role_history"](report["events"])
    (folder / "role-history.json").write_text(json.dumps(roles, indent=2) + "\n")
    (folder / "freeze-evidence.json").write_text(json.dumps(report, indent=1) + "\n")
    try:
        rows, _, later = clock["console"](capture, records, boot)
    except ValueError as exc:
        print("Uncalibrated timeline for", boot, exc)
    else:
        (folder / "timeline.tsv").write_text("".join(f"{t}\t{s}\n" for t,s in rows))
        print(boot, len(rows), "kernel records; later boots", later)
    print(boot, len(report["events"]), "validated journal records")
if not inventory["corroborated_v227"]:
    print("Captured records retained. No v227 boot is corroborated by both console histories.")
