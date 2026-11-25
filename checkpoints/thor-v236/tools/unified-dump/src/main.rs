//! Dump entries of an iOS/macOS unified-log archive (.logarchive) into a TSV.
//!
//! usage: unified-dump <archive.logarchive> <boot-uuid|any> <start-epoch-us> <end-epoch-us> <out.tsv|-> [trace] <file.tracev3>...
//!
//! Files are relative to the archive (Persist/…, Special/…, Signpost/…).  Entries from other boots and
//! outside the time window are skipped; ktrace entries (EventType::Trace) are skipped unless `trace`
//! is given.  Columns: epoch_us, time-of-day (device clock), pid, process, thread, activity, log_type,
//! event_type, subsystem, category, message.  (The 8th column is the file's boot UUID.)
use std::env;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::process::ExitCode;

use macos_unifiedlogs::log_entry::EventType;
use macos_unifiedlogs::logarchive::visit_logarchive_tracev3_files;

fn clean(s: &str) -> String {
    s.chars().map(|c| match c {
        '\t' | '\n' | '\r' => ' ',
        c if (c as u32) < 0x20 => ' ',
        c => c,
    }).collect()
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() < 5 {
        eprintln!("usage: unified-dump <archive> <boot-uuid|any|survey> <start_us> <end_us> <out.tsv|-> [trace] <file.tracev3>...");
        return ExitCode::from(2);
    }
    let archive = Path::new(&args[0]);
    let boot = args[1].to_ascii_lowercase();
    let start: i64 = args[2].parse().expect("start_us");
    let end: i64 = args[3].parse().expect("end_us");
    let mut out: Box<dyn Write> = if args[4] == "-" {
        Box::new(std::io::stdout().lock())
    } else {
        Box::new(BufWriter::with_capacity(1 << 22, File::create(&args[4]).expect("create output")))
    };
    let (mut include_trace, mut files, mut boot_fail) = (false, Vec::new(), 0u64);
    for a in &args[5..] {
        if a == "trace" {
            include_trace = true;
        } else {
            files.push(a.clone());
        }
    }
    let (mut seen, mut written) = (0u64, 0u64);
    if boot == "survey" {
        // survey: entries per boot with first/last timestamp (epoch us) — `start`/`end` are ignored
        let mut per: std::collections::BTreeMap<(String, String), (u64, i64, i64)> = std::collections::BTreeMap::new();
        let result = visit_logarchive_tracev3_files(archive, &files, |i, e| {
            let ts = e.timestamp().timestamp_micros();
            let key = (e.boot_uuid.to_string(), files[i].clone());
            let rec = per.entry(key).or_insert((0, ts, ts));
            rec.0 += 1;
            rec.1 = rec.1.min(ts);
            rec.2 = rec.2.max(ts);
        });
        for ((uuid, file), (n, lo, hi)) in per {
            println!("{uuid}\t{file}\t{n}\t{lo}\t{hi}");
        }
        return match result {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => { eprintln!("error: {e}"); ExitCode::FAILURE }
        };
    }
    let result = visit_logarchive_tracev3_files(archive, &files, |_i, e| {
        seen += 1;
        if boot != "any" && !e.boot_uuid.to_string().eq_ignore_ascii_case(&boot) {
            boot_fail += 1;
            return;
        }
        let ts = e.timestamp().timestamp_micros();
        if ts < start || ts > end {
            return;
        }
        if !include_trace && e.event_type == EventType::Trace {
            return;
        }
        written += 1;
        let (secs, us) = (ts.div_euclid(1_000_000), ts.rem_euclid(1_000_000));
        let msg = clean(&e.message());
        let _ = writeln!(
            out,
            "{}\t{}:{:02}:{:02}.{:06}\t{}\t{}\t{}\t{}\t{}\t{:?}\t{:?}\t{}\t{}\t{}",
            ts,
            secs / 3600,
            (secs / 60) % 60,
            secs % 60,
            us,
            e.pid,
            clean(e.process.unwrap_or("")),
            e.thread_id,
            e.activity_id,
            e.boot_uuid,
            e.log_type,
            e.event_type,
            e.subsystem.unwrap_or(""),
            e.category.unwrap_or(""),
            msg
        );
    });
    let _ = boot_fail;
    match result {
        Ok(()) => {
            eprintln!("entries seen {seen}, written {written}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("error: {e} (seen {seen}, written {written})");
            ExitCode::FAILURE
        }
    }
}
