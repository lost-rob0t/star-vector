#!/usr/bin/env python3
"""Synthetic, full-durability benchmark. Reports measurements, not pass thresholds."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from storage_integration import Client, children, document


def rss(pid):
    try:
        data = Path(f"/proc/{pid}/status").read_text().splitlines()
        return int(next(line.split()[1] for line in data if line.startswith("VmRSS:")))
    except (FileNotFoundError, StopIteration):
        return 0


def run(library, counts):
    client = Client(library)
    rows = []
    for count in counts:
        with tempfile.TemporaryDirectory(prefix="sv-benchmark-") as root:
            path = str(Path(root) / "store")
            worker = subprocess.Popen([os.environ["STAR_VECTOR_TEK9_WORKER"]],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            def request(value):
                worker.stdin.write(json.dumps(value) + "\n"); worker.stdin.flush()
                result = json.loads(worker.stdout.readline())
                assert result["ok"], result
                return result
            started = time.perf_counter()
            request(dict(op="open", path=path, dimensions=3, source="synthetic-benchmark", total=count))
            startup = time.perf_counter() - started
            start = time.perf_counter()
            for _ in range(1000): request(dict(op="flush"))
            ipc = (time.perf_counter() - start) / 1000
            start = time.perf_counter()
            for offset in range(0, count, 128):
                batch = []
                for i in range(offset, min(offset + 128, count)):
                    doc = document(f"bench:{i:08}")
                    doc["rawContent"] = "x" * 1024
                    batch.append(dict(id=doc["id"], record=json.dumps(dict(document=doc, vector=[1, i % 7, 0]))))
                request(dict(op="migrate-batch", expected=offset, records=batch))
            write_seconds = time.perf_counter() - start
            request(dict(op="publish"))
            request(dict(op="close")); worker.wait(timeout=10)

            before = children()
            start = time.perf_counter(); handle = client.open(path)
            reopen = time.perf_counter() - start
            worker_pid, = children() - before
            peaks = [rss(os.getpid()), rss(worker_pid)]
            stop = threading.Event()
            def sample():
                while not stop.wait(0.005):
                    peaks[0] = max(peaks[0], rss(os.getpid()))
                    peaks[1] = max(peaks[1], rss(worker_pid))
            sampler = threading.Thread(target=sample); sampler.start()
            start = time.perf_counter()
            results = client.search(handle, 1, 10)
            search_seconds = time.perf_counter() - start
            stop.set(); sampler.join()
            assert len(results) == min(10, count)
            assert results[0]["id"] == "bench:00000000"
            client.check(client.lib.sv_close(handle))

            # Same stored rows, producer-only cursor/decode time (no pipe or Nim work).
            root_source = Path(__file__).resolve().parents[1]
            script = root_source / "benchmarks/tek9_native.lisp"
            output = subprocess.check_output([
                "sbcl", "--noinform", "--non-interactive", "--no-userinit", "--no-sysinit",
                "--eval", "(require :asdf)",
                "--eval", "(let ((*standard-output* *error-output*)) (asdf:load-system :tek9))",
                "--load", str(script)], env=os.environ | {"SV_BENCH_STORE": path}, text=True)
            native = json.loads(output.strip().splitlines()[-1])
            assert native["records"] == count
            # Stream the same rows through the worker, omitting Nim scoring/validation.
            worker = subprocess.Popen([os.environ["STAR_VECTOR_TEK9_WORKER"]],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            request(dict(op="open", path=path, dimensions=3))
            start = time.perf_counter()
            worker.stdin.write('{"op":"scan"}\n'); worker.stdin.flush()
            scanned = 0
            while True:
                response = json.loads(worker.stdout.readline())
                assert response["ok"], response
                if response.get("end"): break
                scanned += 1
            bridge_scan = time.perf_counter() - start
            assert scanned == count
            request(dict(op="close")); worker.wait(timeout=10)
            rows.append(dict(records=count, dimensions=3, payload_bytes=1024, k=10,
                             startup_seconds=startup, reopen_seconds=reopen,
                             ipc_noop_seconds=ipc, batch_write_seconds=write_seconds,
                             native_scan_seconds=native["native_scan_seconds"],
                             bridge_scan_seconds=bridge_scan, search_seconds=search_seconds,
                             parent_peak_rss_kib=peaks[0], worker_peak_rss_kib=peaks[1],
                             store_bytes=Path(path, "data.mdb").stat().st_size))
    return dict(durability="full", tek9="e6d1cb86a2e268a894eae3acae9617238b2adcff",
                notes=["RSS includes Python driver, SBCL heap and LMDB mapped pages; it is not a heap bound.",
                       "Native scan includes Tek9 Conspack decode; bridge scan adds pipe/JSON/Python overhead.",
                       "Search adds Nim schema validation, vector arithmetic and top-k; timings are separate paths, not additive.",
                       "Batch writes use synthetic adapter records, excluding Nim ingress validation.",
                       "Exact search is O(N*d + N*log(k)); retained result heap is O(k), excluding largest row and runtime heaps."], rows=rows)


if __name__ == "__main__":
    result = run(sys.argv[1], [int(n) for n in sys.argv[2].split(",")])
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
