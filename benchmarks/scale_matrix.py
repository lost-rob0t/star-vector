#!/usr/bin/env python3
"""Serialize the scale matrix and preserve failures, resource limits and exact commands."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--qdrant-binary", required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--lane", choices=["algorithm", "database", "all"], default="all")
    parser.add_argument("--resume", action="store_true", help="Preserve and skip completed result files in an existing output directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=args.resume)
    script = str(Path(__file__).with_name("scale.py"))
    ledger = args.output / "matrix.jsonl"
    cases = [("10k128-v2", 10000, 128), ("50k128", 50000, 128), ("100k128", 100000, 128),
             ("1m128", 1000000, 128), ("50k384", 50000, 384), ("50k768", 50000, 768),
             ("50k1536", 50000, 1536), ("100k128-uniform", 100000, 128)]
    plans = []
    for case, n, d in cases:
        if args.lane in ("algorithm", "all"):
            plans.extend((case, n, d, "faiss", method, 1000) for method in ("flat", "hnsw", "ivf", "pq"))
            plans.append((case, n, d, "starvector", "transient", 1000 if n <= 100000 and d <= 384 else 100 if n < 1000000 else 20))
        if args.lane in ("database", "all") and case != "100k128-uniform":
            plans.extend((case, n, d, backend, "tek9" if backend == "starvector" else "default", 1000 if backend != "starvector" else 3)
                         for backend in ("sqlite", "qdrant", "starvector"))
    for case, n, d, backend, method, queries in plans:
        name = f"{case}-{backend}-{method}"
        target = args.output / (name + ".json")
        command = [sys.executable, script, "run", "--data", str(args.data_root / case), "--output", str(target),
                   "--backend", backend, "--method", method, "--queries", str(queries),
                   "--library", args.library, "--qdrant-binary", args.qdrant_binary]
        if case == "50k128" and backend != "faiss" and method != "transient":
            command.append("--lifecycle")
        free = shutil.disk_usage(args.output).free
        # Conservative disk admission, based on float JSON plus payload and LMDB
        # overflow pages. This is an estimate, not a measured capacity claim.
        estimate = n * (d * 32 + 8192) if method == "tek9" else n * (d * 8 + 4096)
        row = dict(name=name, command=command, started=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   disk_free_bytes=free, estimated_working_disk_bytes=estimate, timeout_seconds=args.timeout)
        if args.resume and target.exists():
            prior = json.loads(target.read_text())
            if prior.get("status") == "completed":
                print(json.dumps(dict(event="preserved", name=name)), flush=True)
                continue
            raise SystemExit(f"Prior failed result retained: {target}; choose a new output directory for retries")
        # Admit expensive exact durable cells from measured smaller cells on
        # this same machine. Extrapolation is only a scheduling estimate.
        observed = []
        if method == "tek9":
            for previous in args.output.glob("*-starvector-tek9.json"):
                measurement = json.loads(previous.read_text())
                if measurement.get("status") != "completed":
                    continue
                previous_n = measurement["manifest"]["records"]
                previous_d = measurement["manifest"]["dimensions"]
                factor = (n / previous_n) * max(1, d / previous_d)
                scan_seconds = measurement["filters"][0]["p50_ms"] / 1000
                predicted = factor * (measurement["build_seconds"] + scan_seconds * (queries + 3))
                observed.append((predicted, previous.name))
        if observed:
            predicted, source = max(observed)
            row.update(estimated_seconds=predicted, estimate_source=source,
                       estimate_note="Linear row/dimension scheduling estimate; not an executed result or scalability guarantee")
        print(json.dumps(dict(event="start", **row)), flush=True)
        if observed and predicted > args.timeout:
            row.update(status="skipped", reason="Observed smaller-cell ingest/search cost projects beyond per-cell time budget")
        elif free < estimate + 2 * 1024**3:
            row.update(status="skipped", reason="Estimated working disk plus 2 GiB headroom exceeds actual free space")
        else:
            start = time.perf_counter()
            with target.with_suffix(".log").open("w") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = process.wait(timeout=args.timeout)
                    row.update(status="completed" if code == 0 else "failed", returncode=code)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait()
                    row.update(status="timeout", reason="Cell exceeded recorded time budget; no result inferred")
            row["wall_seconds"] = time.perf_counter()-start
            # Healthy child exits clean only their exclusively-created temporary
            # stores. Do not infer ownership of any neighboring path from a name.
            if row["status"] == "timeout":
                row["scratch_note"] = "Killed child may leave its new sv-* temporary directory; result files and unrelated paths are retained"
        with ledger.open("a") as stream:
            stream.write(json.dumps(row)+"\n")
        print(json.dumps(dict(event="end", **row)), flush=True)


if __name__ == "__main__":
    main()
