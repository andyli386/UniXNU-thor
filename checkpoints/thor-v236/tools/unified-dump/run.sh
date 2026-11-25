#!/bin/bash
# Dump a time window of one boot's unified log to TSV.
# usage: run.sh <archive.logarchive> <boot-uuid|any> <start-epoch-us> <end-epoch-us> <out.tsv> [trace]
set -euo pipefail
bin=$(cd "$(dirname "$0")" && pwd)/target/release/thor-unified-dump
archive=$1; boot=$2; start=$3; end=$4; out=$5; shift 5
files=()
for d in Persist Special Signpost HighVolume; do
  [ -d "$archive/$d" ] || continue
  for f in "$archive/$d"/*.tracev3; do
    [ -e "$f" ] && files+=("$d/$(basename "$f")")
  done
done
echo "files: ${#files[@]}" >&2
exec "$bin" "$archive" "$boot" "$start" "$end" "$out" "$@" "${files[@]}"
