#!/usr/bin/env python3
"""Summarize a THOR recovery export without changing the raw evidence."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


RUNTIME = "pong/libpong-ui-v77.dylib"
RUNTIME_UUID = "9FCCBB70-F843-3FF8-AB78-DAF5B4582068"
RUNTIME_BYTES = 197872
MIB = 1024 * 1024
BOOT_UUID = rb"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
CONSOLE_BEGIN = re.compile(rb"^Pong console capture BEGIN boot=(" + BOOT_UUID + rb"|unavailable)\b")
CONSOLE_TIME = re.compile(rb"^\[\s*(\d+)\.(\d{6})\]:")
RAM_SUMMARY = re.compile(rb"Pong RAM: (\d+) owned banks, (\d+) MiB managed before kernel reservations")
RAM_BANK = re.compile(rb"Pong RAM: bank (\d+) (0x[0-9a-fA-F]+) \+ (0x[0-9a-fA-F]+)")
RUNTIME_STAGE = re.compile(
    rb"THOR UI runtime: (PASS) path=/private/var/pong/libpong-ui-v77\.dylib bytes=\d+\b"
    rb"|THOR UI runtime: (FAIL) error=\d+; using existing adapter"
)
LOADED_ADAPTER = re.compile(rb"^Pong boot adapter: (\S+) pid=(\d+) boot=(" + BOOT_UUID + rb") uuid=(" + BOOT_UUID + rb"|unavailable) path=(.+)$")
SESSION = re.compile(rb"^Pong boot snapshot SESSION: (\S+) pid=(\d+) boot=(" + BOOT_UUID + rb"|unavailable)\b")
LIMIT = re.compile(rb"Pong lsd memlimit: pid=(\d+) active=(-?\d+)MiB active_attr=(0x[0-9a-fA-F]+) inactive=(-?\d+)MiB inactive_attr=(0x[0-9a-fA-F]+)")
EXIT = re.compile(rb"(?:audiomxd|PosterBoard|lsd|SpringBoard|backboardd)", re.I)


@dataclass(frozen=True)
class Line:
    file: str
    number: int
    raw: bytes
    boot: str | None = None

    @property
    def clock_us(self):
        match = CONSOLE_TIME.match(self.raw)
        if match:
            return int(match[1]) * 1_000_000 + int(match[2])
        return None

    def evidence(self):
        record = {"file": self.file, "line": self.number,
                  "raw": self.raw.decode("ascii", errors="backslashreplace")}
        if self.clock_us is not None:
            record["kernel_us"] = self.clock_us
        return record


def receipt(status, meaning, evidence=(), **details):
    return {"status": status, "meaning": meaning,
            "evidence": [item.evidence() if isinstance(item, Line) else item for item in evidence],
            **details}


def file_contents(path, root):
    # Only LF is a delimiter. Embedded NUL and malformed text remain visible.
    name = str(path.relative_to(root))
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            yield Line(name, number, raw.rstrip(b"\n"))


def collect_legacy_suffix(root, prefix_path):
    candidates = sorted(root.rglob("PongConsole.txt"))
    if len(candidates) != 1:
        raise ValueError("--previous-console requires exactly one current PongConsole.txt")
    current = candidates[0]
    prefix = prefix_path.read_bytes()
    full = current.read_bytes()
    if not prefix or len(prefix) >= len(full) or not full.startswith(prefix):
        raise ValueError("previous console is not a strict byte prefix of the current file")
    suffix = full[len(prefix):]
    offset = prefix.count(b"\n")
    discarded = 0
    if not prefix.endswith(b"\n"):
        partial, separator, suffix = suffix.partition(b"\n")
        if not separator:
            raise ValueError("current file only continues the previous partial line")
        offset += 1
        discarded = len(partial) + 1
    file = str(current.relative_to(root))
    lines = [Line(file, number, raw) for number, raw in enumerate(suffix.split(b"\n"), offset + 1)]
    if not lines[-1].raw:
        lines.pop()
    return lines, {
        "files": [file],
        "mode": "legacy-verified-byte-suffix",
        "boot_labels": [],
        "legacy_lines": len(lines),
        "raw_physical_lines": full.count(b"\n") + int(not full.endswith(b"\n")),
        "invalid_timestamp_lines": sum(line.raw.startswith(b"[") and line.clock_us is None for line in lines),
        "nul_lines": sum(b"\0" in line.raw for line in lines),
        "verified_previous_bytes": len(prefix),
        "discarded_partial_line_bytes": discarded,
        "previous_console": str(prefix_path),
    }


def collect_console(root, requested_boot):
    current_boot = None
    seen_boots = []
    console = []
    files = [*sorted(root.rglob("PongConsole.previous.txt")), *sorted(root.rglob("PongConsole.txt"))]
    for path in files:
        previous_boot = current_boot if path.name == "PongConsole.txt" else None
        current_boot = None
        first_record = True
        for line in file_contents(path, root):
            match = CONSOLE_BEGIN.match(line.raw)
            if match:
                current_boot = match[1].decode().lower()
                seen_boots.append(current_boot)
            if first_record and line.raw.strip():
                first_record = False
                # The writer prepends LF, so ROTATE is the first nonempty
                # record. It alone can link this prefix to the previous file.
                if re.fullmatch(rb"Pong console capture ROTATE next=\d+", line.raw):
                    current_boot = previous_boot
            console.append(Line(line.file, line.number, line.raw, current_boot))

    latest = seen_boots[-1] if seen_boots else None
    boot = requested_boot or latest
    if boot:
        selected = [line for line in console if line.boot == boot]
        mode = "cursor" if boot != "unavailable" else "cursor-without-uuid"
    else:
        # The legacy reader replayed entire rings. Take a single repeated RAM
        # anchor only when it identifies an unambiguous legacy section.
        anchors = {line.raw for line in console if RAM_SUMMARY.search(line.raw)}
        selected = console if len(anchors) == 1 else []
        mode = "legacy-single-anchor" if selected else "legacy-unresolved"

    return selected, boot, {
        "files": [str(path.relative_to(root)) for path in files],
        "mode": mode,
        "boot_labels": list(dict.fromkeys(seen_boots)),
        "legacy_lines": sum(line.boot is None for line in console),
        "raw_physical_lines": len(console),
        "invalid_timestamp_lines": sum(line.raw.startswith(b"[") and line.clock_us is None for line in selected),
        "nul_lines": sum(b"\0" in line.raw for line in selected),
    }


def macho_uuid(path):
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32 or header[:4] != b"\xcf\xfa\xed\xfe":
            raise ValueError("expected a thin, little-endian 64-bit Mach-O")
        count, length = struct.unpack_from("<II", header, 16)
        if length > 1024 * 1024 or count > length // 8:
            raise ValueError("invalid Mach-O load command extent")
        commands = stream.read(length)
    if len(commands) != length:
        raise ValueError("truncated Mach-O load commands")
    offset = 0
    for _ in range(count):
        if offset + 8 > length:
            raise ValueError("truncated Mach-O load command header")
        kind, size = struct.unpack_from("<II", commands, offset)
        if size < 8 or size > length - offset:
            raise ValueError("invalid Mach-O load command size")
        if kind == 0x1B and size == 24:
            return str(UUID(bytes=commands[offset + 8:offset + 24])).upper()
        offset += size
    raise ValueError("no LC_UUID command")


def runtime_check(root, lines, expected_uuid, expected_bytes):
    staging = [(line, RUNTIME_STAGE.search(line.raw)) for line in lines]
    staging = [(line, match[1] or match[2]) for line, match in staging if match]
    stage_lines = [line for line, _ in staging]
    path = root / RUNTIME
    exported = {"file": RUNTIME, "expected_uuid": expected_uuid, "expected_bytes": expected_bytes}
    if path.is_file():
        try:
            exported.update(uuid=macho_uuid(path), bytes=path.stat().st_size)
        except (OSError, ValueError) as error:
            exported["error"] = str(error)
    if any(status == b"FAIL" for _, status in staging):
        return receipt("FAIL", "the selected boot reported a runtime staging failure", stage_lines + [exported])
    if not stage_lines:
        return receipt("SKIP", "no runtime staging receipt in the selected kernel log", [exported])
    if not all(re.search(rb"\bbytes=" + str(expected_bytes).encode() + rb"\b", line.raw) for line in stage_lines):
        return receipt("FAIL", "staging receipt size differs from the expected candidate", stage_lines + [exported])
    if "uuid" not in exported:
        return receipt("SKIP", "staging receipt found, exported Mach-O UUID is unavailable", stage_lines + [exported])
    matches = exported["uuid"] == expected_uuid and exported["bytes"] == expected_bytes
    return receipt("PASS" if matches else "FAIL",
                   "staging receipt and exported Mach-O size/UUID match the candidate" if matches
                   else "exported staged runtime differs from the candidate", stage_lines + [exported])


def runtime_loaded_check(observed, boot, expected_uuid):
    required = {b"SpringBoard", b"backboardd"}
    loaded = {}
    for line in observed:
        match = LOADED_ADAPTER.match(line.raw)
        if not match:
            continue
        process, _, session, identity, path = match.groups()
        if process not in required or session.decode().lower() != boot:
            continue
        loaded[process] = (identity.decode().upper(), path, line)
    evidence = [value[2] for value in loaded.values()]
    if not required.issubset(loaded):
        return receipt("SKIP", "same-boot loaded-adapter receipts are incomplete", evidence)
    matched = all(identity == expected_uuid and path == b"/private/var/" + RUNTIME.encode()
                  for identity, path, _ in loaded.values())
    return receipt("PASS" if matched else "FAIL",
                   "SpringBoard and backboardd mapped the candidate adapter" if matched
                   else "at least one core UI process mapped a different adapter",
                   evidence, expected_uuid=expected_uuid)


def memory_check(lines, boot_identified):
    summaries = [(line, RAM_SUMMARY.search(line.raw)) for line in lines]
    summaries = [(line, match) for line, match in summaries if match]
    banks = {}
    conflicts = False
    for line in lines:
        match = RAM_BANK.search(line.raw)
        if match:
            index = int(match[1])
            start, size = (int(value, 16) for value in match.groups()[1:])
            if index in banks and banks[index][:2] != (start, size):
                conflicts = True
            banks[index] = (start, size, line)
    evidence = [line for line, _ in summaries] + [banks[index][2] for index in sorted(banks)]
    summaries_values = {tuple(map(int, match.groups())) for _, match in summaries}
    if not summaries or len(summaries_values) != 1 or conflicts:
        return receipt("SKIP", "missing or ambiguous post-crop RAM receipts", evidence)
    bank_count, reported_mib = next(iter(summaries_values))
    if bank_count < 1 or len(banks) != bank_count or sorted(banks) != list(range(bank_count)):
        return receipt("SKIP", "post-crop RAM bank list is incomplete", evidence)
    ranges = sorted((start, start + size) for start, size, _ in banks.values())
    amount = sum(end - start for start, end in ranges)
    details = {"managed_bytes": amount, "reported_mib": reported_mib,
               "ranges": [{"start": hex(start), "end_exclusive": hex(end)} for start, end in ranges]}
    sane = (all(start < end and start % 4096 == end % 4096 == 0 for start, end in ranges)
            and all(left[1] <= right[0] for left, right in zip(ranges, ranges[1:]))
            and amount // MIB == reported_mib)
    if not sane:
        return receipt("FAIL", "bank ranges and summary are inconsistent", evidence, **details)
    if not boot_identified:
        return receipt("SKIP", "legacy bank evidence lacks v77 boot identity", evidence, **details)
    accepted = (ranges[0][0] == 0x800000000 and ranges[-1][1] <= 0x830000000
                and 665 * MIB < amount <= 768 * MIB)
    return receipt("PASS" if accepted else "FAIL",
                   "v77 managed memory is within the expected owned range" if accepted
                   else "v77 managed memory missed its expected growth or address ceiling", evidence, **details)


def display_check(lines, end_us):
    changes = [line for line in lines if re.search(rb"MiniIOMFB layers (?:EMPTIED|RESTORED):", line.raw)]
    valid = sorted((line for line in changes if line.clock_us is not None), key=lambda line: line.clock_us)
    first_frames = [line for line in lines if b"first visible frame" in line.raw.lower()]
    empties = [line for line in valid if b"layers EMPTIED:" in line.raw]
    details = {"first_frame_events": len(first_frames), "layer_transitions": len(changes)}
    if not empties:
        return receipt("SKIP", "no time-qualified clear and recovery transition", (first_frames + changes)[-10:], **details)
    last_empty = empties[-1]
    restored = [line for line in valid if b"layers RESTORED:" in line.raw and line.clock_us > last_empty.clock_us]
    after_us = end_us - last_empty.clock_us
    if restored:
        return receipt("PASS", "layers were restored after the last recorded clear", [last_empty, restored[0]],
                       **details, UI_visibility="unverified", kernel_activity_after_clear_us=after_us)
    return receipt("FAIL" if after_us >= 300_000_000 else "SKIP",
                   "no layer restore observed after the final clear", [last_empty],
                   **details, UI_visibility="unverified", kernel_activity_after_clear_us=after_us)


def coverage_check(lines, gaps, boot_identified):
    if not lines:
        return receipt("SKIP", "no time-qualified kernel records")
    first, last = lines[0], lines[-1]
    details = {"first_kernel_us": first.clock_us, "last_kernel_us": last.clock_us,
               "cursor_gaps": len(gaps)}
    covered = boot_identified and first.clock_us <= 60_000_000 and last.clock_us >= 1_800_000_000 and not gaps
    return receipt("PASS" if covered else "SKIP",
                   "kernel log covers startup through 30 minutes" if covered
                   else "kernel log does not establish continuous 30 minute coverage",
                   [first, last, *gaps[:5]], **details)


def logs_with_sessions(root, boot):
    sessions, observed, diagnostic = [], [], []
    target_runningboard_pids = set()
    for path in sorted(root.rglob("PongBoot-*.txt")):
        selected = False
        for line in file_contents(path, root):
            match = SESSION.match(line.raw)
            if match:
                name, pid, label = match.groups()
                selected = boot not in (None, "unavailable") and label.decode().lower() == boot
                if selected:
                    sessions.append(line)
                    if name == b"runningboardd":
                        target_runningboard_pids.add(pid)
            elif selected:
                observed.append(line)
    for path in sorted(root.rglob("PongDiag.txt")):
        section = []
        aligned = False
        for line in file_contents(path, root):
            match = re.match(rb"Pong diag: worker start pid=(\d+)", line.raw)
            if match:
                section = []
                aligned = match[1] in target_runningboard_pids
            if aligned:
                section.append(line)
        diagnostic.extend(section)
    return sessions, observed, diagnostic


def daemon_check(lines):
    exits, counts = [], Counter()
    for line in lines:
        if b"PONG EXIT" not in line.raw and b"memorystatus" not in line.raw:
            continue
        if not EXIT.search(line.raw):
            continue
        kind = next((kind for kind in (b"process-launch", b"highwater", b"compressor-thrashing",
                                       b"long-idle-exit", b"idle-exit", b"stackshot", b"RPC timeout")
                     if kind in line.raw), b"other exit")
        counts[kind.decode()] += 1
        if kind not in (b"idle-exit", b"long-idle-exit"):
            exits.append(line)
    failures = [line for line in exits if (b"PosterBoard" in line.raw and b"process-launch" in line.raw)
                or (b"audiomxd" in line.raw and (b"signal=6" in line.raw or b"stackshot" in line.raw))
                or (b"lsd" in line.raw and b"highwater" in line.raw)]
    return receipt("FAIL" if failures else "SKIP",
                   "selected known daemon failures appeared" if failures
                   else "no targeted daemon failure identified; readiness is unverified",
                   (failures if failures else exits)[:20], counts=dict(counts))


def memlimit_check(diagnostic):
    samples = {}
    for line in diagnostic:
        match = LIMIT.search(line.raw)
        if match:
            pid, active, active_attr, inactive, inactive_attr = match.groups()
            samples[int(pid)] = (int(active), int(inactive), active_attr.decode(), inactive_attr.decode(), line)
    if not samples:
        failures = [line for line in diagnostic if b"Pong lsd memlimit:" in line.raw]
        return receipt("SKIP", "no usable lsd limit readback in the matched runningboardd session", failures[:10])
    last = max(samples.values(), key=lambda sample: sample[4].number)
    matches = all(sample[:2] == (32, 12) for sample in samples.values())
    return receipt("PASS" if matches else "FAIL",
                   "each sampled lsd PID ended at 32/12 MiB" if matches
                   else "at least one lsd PID had a different final sampled limit",
                   [sample[4] for sample in samples.values()],
                   sampled_pids=len(samples), active_mib=last[0], inactive_mib=last[1],
                   active_attr=last[2], inactive_attr=last[3],
                   provenance="most recent diagnostics worker matched to same-boot runningboardd PID")


def migration_check(root, sessions):
    migration = root / "mobile/Library/Preferences/com.apple.migration.plist"
    state = {"file": str(migration.relative_to(root))}
    if migration.is_file():
        try:
            with migration.open("rb") as stream:
                results = plistlib.load(stream).get("DMLastMigrationResults")
            if isinstance(results, dict):
                state.update(success=results.get("success"), build=results.get("buildVersion"))
        except (OSError, ValueError, plistlib.InvalidFileException) as error:
            state["error"] = str(error)
    pids = set()
    for line in sessions:
        match = SESSION.match(line.raw)
        if match and match[1] == b"com.apple.datamigrator":
            pids.add(match[2].decode())
    events = []
    if pids:
        for path in sorted(root.rglob("PongDM-*.txt")):
            if path.stem.removeprefix("PongDM-") not in pids:
                continue
            # Per-PID log names can recur across boots. Require monotonically
            # increasing records in the final process segment and an in-scope
            # current-boot sampler session before trusting final results.
            segment = []
            previous_ms = None
            for line in file_contents(path, root):
                match = re.search(rb"\bmono_ns=(\d+)\b", line.raw)
                if not match:
                    continue
                current_ms = int(match[1]) // 1_000_000
                if previous_ms is not None and current_ms < previous_ms:
                    segment = []
                previous_ms = current_ms
                segment.append(line)
            events.extend(segment)
    plugin = [line for line in events if b"Pong DM: plugin " in line.raw]
    finals = [(line, re.search(rb"Pong DM: client results sent success=([01])", line.raw)) for line in events]
    finals = [(line, match[1] == b"1") for line, match in finals if match]
    details = {"plugin_begin": sum(b"plugin begin" in line.raw for line in plugin),
               "plugin_end": sum(b"plugin end" in line.raw for line in plugin),
               "plugin_failures": sum(b"plugin end" in line.raw and b"success=0" in line.raw for line in plugin)}
    evidence = [*plugin[:16], *(line for line, _ in finals), state]
    if not finals:
        return receipt("SKIP", "persistent migration state lacks a client result from a matching datamigrator PID", evidence, **details)
    terminal = {value for _, value in finals}
    if len(terminal) != 1:
        return receipt("FAIL", "migration client result receipts disagree", evidence, **details)
    success = next(iter(terminal))
    if not success or state.get("success") is False:
        return receipt("FAIL", "migration reported failure or inconsistent persistent state", evidence, **details)
    if state.get("success") is not True or details["plugin_begin"] != details["plugin_end"]:
        return receipt("SKIP", "successful client result lacks complete corroborating receipts", evidence, **details)
    return receipt("SKIP", "client result and persistent state agree; the per-PID migration log lacks a boot UUID",
                   evidence, **details)


def observer_check(sessions, observed, diagnostic):
    call = [line for line in observed if b"Pong UI call " in line.raw]
    trigger = [line for line in observed if b"Pong boot display trigger:" in line.raw]
    source = [line for line in diagnostic if b"source=kernel-console" in line.raw]
    omissions = [line for line in observed if re.search(rb"Pong .*(?:overflow|budget exhausted|trigger (?:FAIL|SKIP)|snapshot FAIL)", line.raw)]
    details = {"session_count": len(sessions), "call_receipts": len(call), "trigger_captures": len(trigger),
               "trigger_posts": len(source), "probe_omissions": len(omissions)}
    working = bool(call) and bool(trigger) and not omissions
    return receipt("PASS" if working else "SKIP",
                   "call receipts and display trigger capture are present" if working
                   else "observer evidence or display-trigger capture is incomplete",
                   [*sessions[:8], *call[:8], *source[:5], *trigger[:5], *omissions[:8]], **details)


def mount_check(lines):
    system = [line for line in lines if b"systemos mounted" in line.raw.lower()]
    data = [line for line in lines if b"data mount pass" in line.raw.lower()]
    injection = [line for line in lines if b"THOR INJECT: armed" in line.raw or b"THOR INJECT armed" in line.raw]
    failures = [line for line in lines if re.search(rb"Data mount (?:FAIL|SKIP)", line.raw, re.I)]
    status = "FAIL" if failures else "PASS" if system and data and injection else "SKIP"
    return receipt(status, "look for SystemOS, Data mount, and armed injection receipts",
                   [*(system + data + injection)[:8], *failures[:8]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="capture directory containing extracted/files, extracted, or files")
    parser.add_argument("--boot-id", type=lambda value: str(UUID(value)), help="select a specific cursor boot UUID")
    parser.add_argument("--previous-console", type=Path,
                        help="require this exact prior byte prefix and analyze only the complete appended legacy lines")
    parser.add_argument("--runtime-candidate", type=Path,
                        help="read the expected runtime Mach-O identity instead of the v77 reference")
    parser.add_argument("--output", type=Path, help="optional JSON destination; otherwise write to stdout")
    args = parser.parse_args()
    directory = args.directory.absolute()
    root = next((path for path in (directory / "extracted" / "files", directory / "files", directory)
                 if path.is_dir() and (path.name == "files" or (path / "logs").is_dir())), None)
    if root is None:
        parser.error("directory does not contain extracted/files, files, or a raw logs directory")
    if args.previous_console:
        if args.boot_id:
            parser.error("--previous-console and --boot-id select different evidence modes")
        try:
            raw, console = collect_legacy_suffix(root, args.previous_console)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        boot = None
    else:
        raw, boot, console = collect_console(root, args.boot_id)
    timed = [line for line in raw if line.clock_us is not None]
    if console["mode"].startswith("legacy-"):
        timed = list({line.raw: line for line in reversed(timed)}.values())
    lines = sorted(timed, key=lambda line: line.clock_us)
    issues = [line for line in raw if re.match(rb"Pong console capture (?:WRAP|RESET)", line.raw)]
    selected_boot = boot not in (None, "unavailable")
    unique_raw = list({line.raw: line for line in reversed(raw)}.values()) if not selected_boot else raw
    expected_uuid, expected_bytes = RUNTIME_UUID, RUNTIME_BYTES
    if args.runtime_candidate:
        try:
            expected_uuid = macho_uuid(args.runtime_candidate)
            expected_bytes = args.runtime_candidate.stat().st_size
        except (OSError, ValueError) as error:
            parser.error(f"--runtime-candidate: {error}")
    stage = runtime_check(root, unique_raw, expected_uuid, expected_bytes)
    candidate_kernel = bool(args.previous_console and
                            sum(bool(RUNTIME_STAGE.search(line.raw)) for line in unique_raw) == 1 and
                            sum(bool(RAM_SUMMARY.search(line.raw)) for line in unique_raw) == 1)
    identified = (selected_boot or candidate_kernel) and stage["status"] == "PASS"
    sessions, observed, diagnostic = logs_with_sessions(root, boot)
    display = display_check(unique_raw, lines[-1].clock_us if lines else 0)
    if display["status"] == "FAIL" and (issues or not (selected_boot or candidate_kernel)):
        display["status"] = "SKIP"
        display["meaning"] += "; cursor continuity or boot identity is unverified"
    checks = {
        "runtime_staging": stage,
        "runtime_loaded": runtime_loaded_check(observed, boot, expected_uuid),
        "mount_and_injection": mount_check(unique_raw),
        "managed_memory": memory_check(unique_raw, identified or candidate_kernel),
        "lsd_runtime_limits": memlimit_check(diagnostic),
        "layer_recovery": display,
        "daemon_faults": daemon_check(lines),
        "migration": migration_check(root, sessions),
        "observers": observer_check(sessions, observed, diagnostic),
        "kernel_log_coverage": coverage_check(lines, issues, identified),
        "visible_ui_and_input": receipt("SKIP", "requires real display and input observations for five minutes"),
    }
    result = {"export_root": str(root), "boot_id": boot, "candidate_identified": identified,
              "candidate_kernel_identified": identified or candidate_kernel,
              "console": console, "checks": checks,
              "overall": "FAIL" if (((selected_boot or candidate_kernel) and stage["status"] == "FAIL")
                                    or (identified and any(check["status"] == "FAIL" for check in checks.values())))
              else "SKIP"}
    encoded = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        destination = args.output.absolute()
        if destination.resolve().is_relative_to(root.resolve()):
            parser.error("output must be outside the raw evidence directory")
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
