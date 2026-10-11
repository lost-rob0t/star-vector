#!/usr/bin/env python3
"""Export inspectable synthetic results and tabular summaries; never package databases."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extra", type=Path, action="append", default=[])
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--lisp-dependencies", type=Path)
    parser.add_argument("--supersede", action="append", default=[], help="Retain this result in raw evidence but omit it from the summary")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = sorted(args.results.glob("*.json")) + args.extra
    results = []
    for path in files:
        value = json.loads(path.read_text())
        results.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            included_in_summary=path.name not in args.supersede, result=value))
    ledger = [json.loads(line) for line in (args.results / "matrix.jsonl").read_text().splitlines()]
    bundle = dict(results=results, execution_ledger=ledger, dependencies=json.loads(args.dependencies.read_text()),
                  notes=["Only synthetic result JSON is exported, never vector datasets or databases.",
                         "Commands retain the actual local paths used for execution.",
                         "No p99 claim is supported by rows marked p99_supported=false.",
                         "Algorithm, transient C ABI, embedded DB, worker-backed DB and HTTP DB paths have different costs."])
    if args.lisp_dependencies:
        bundle["dependencies"]["local_lisp_sources"] = json.loads(args.lisp_dependencies.read_text())
    encoded = (json.dumps(bundle, sort_keys=True, separators=(",", ":"))+"\n").encode()
    # Deterministic compression and a digest make the full evidence bundle
    # inspectable without committing thousands of repeated pretty-JSON lines.
    archive = args.output / "raw-results.json.gz"
    archive.write_bytes(gzip.compress(encoded, mtime=0))
    (args.output / "checksums.json").write_text(json.dumps({archive.name: hashlib.sha256(archive.read_bytes()).hexdigest(),
                                                         "uncompressed_json_sha256": hashlib.sha256(encoded).hexdigest()}, indent=2)+"\n")
    columns = ["source", "backend", "method", "lane", "records", "dimensions", "distribution", "bucket_limit", "eligible_records",
               "samples", "p50_ms", "p95_ms", "p99_ms", "p99_supported", "qps_serial", "id_recall", "tie_recall", "invalid_results",
               "build_seconds", "disk_bytes", "peak_rss_kib", "peak_pss_kib", "peak_anonymous_kib", "peak_file_rss_kib", "status"]
    with (args.output / "measurements.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n"); writer.writeheader()
        for item in results:
            if not item["included_in_summary"]:
                continue
            row = item["result"]
            if "backend" not in row:
                continue
            manifest = row.get("manifest", {})
            base = dict(source=item["file"], **{key: row.get(key) for key in ("backend", "method", "lane", "build_seconds", "disk_bytes", "status")},
                        **{key: manifest.get(key) for key in ("records", "dimensions", "distribution")})
            base.update({"peak_"+key: value for key, value in row.get("memory", {}).get("peak_sum_kib", {}).items()})
            for measurement in (row.get("filters") or [{}]):
                fields = {key: value for key, value in measurement.items() if key in columns}
                writer.writerow(base | fields)
    (args.output / "execution-ledger.json").write_text(json.dumps(ledger, indent=2)+"\n")
    (args.output / "dependencies.json").write_text(json.dumps(bundle["dependencies"], indent=2)+"\n")
    print(json.dumps(dict(result_files=len(results), compressed_bytes=archive.stat().st_size,
                          completed_in_summary=sum(item["included_in_summary"] and item["result"].get("status") == "completed" for item in results),
                          failed=sum(item["result"].get("status") == "failed" for item in results))))


if __name__ == "__main__":
    main()
