#!/usr/bin/env python3
"""Matched synthetic baseline/candidate profiles; no production paths are used."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from storage_integration import Client, children, document

COUNT, DIMENSIONS, K = 50000, 128, 10
QUERY = [1.0] + [0.0] * (DIMENSIONS - 1)
FIELDS = ("Rss", "Pss", "Pss_Anon", "Pss_File", "Anonymous", "Private_Dirty", "Shared_Clean")


def memory(pid, mappings=False):
    result = {key + "_kib": 0 for key in FIELDS}
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in FIELDS: result[key + "_kib"] = int(value.split()[0])
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"): result["rss_lifetime_hwm_kib"] = int(line.split()[1])
        if mappings:
            groups = {}
            group = None
            for line in Path(f"/proc/{pid}/smaps").read_text().splitlines():
                if re.match(r"^[0-9a-f]+-[0-9a-f]+ ", line):
                    parts = line.split(maxsplit=5)
                    path = parts[5] if len(parts) == 6 else ""
                    category = ("lmdb" if path.endswith("/data.mdb") else
                                "worker_image" if "star-vector-tek9-worker" in path else
                                "other_file" if path.startswith("/") else "anonymous_mapping")
                    group = groups.setdefault(category, {key + "_kib": 0 for key in FIELDS})
                elif group is not None:
                    key, _, value = line.partition(":")
                    if key in FIELDS: group[key + "_kib"] += int(value.split()[0])
            result["mappings"] = groups
    except FileNotFoundError:
        pass
    return result


def capture(pids):
    parent = memory(os.getpid(), True)
    workers = {str(pid): memory(pid, True) for pid in pids}
    totals = {key + "_kib": parent[key + "_kib"] + sum(w[key + "_kib"] for w in workers.values())
              for key in FIELDS}
    return dict(parent=parent, workers=workers, sum_process_metrics=totals)


def measured(pids, operation):
    peaks = {str(pid): memory(pid) for pid in [os.getpid(), *pids]}
    stop = threading.Event()
    def sample():
        while not stop.wait(0.01):
            for pid in [os.getpid(), *pids]:
                for key, value in memory(pid).items():
                    peaks[str(pid)][key] = max(peaks[str(pid)].get(key, 0), value)
    thread = threading.Thread(target=sample)
    thread.start()
    started = time.perf_counter()
    try:
        value = operation()
    finally:
        seconds = time.perf_counter() - started
        stop.set(); thread.join()
    for pid in [os.getpid(), *pids]:
        for key, value in memory(pid).items():
            peaks[str(pid)][key] = max(peaks[str(pid)].get(key, 0), value)
    return value, dict(seconds=seconds, sampled_peaks=peaks, steady=capture(pids))


def events(directory, pid):
    try: lines = (directory / f"{pid}.jsonl").read_text().splitlines()
    except FileNotFoundError: return []
    result = []
    for line in lines:
        try: result.append(json.loads(line))
        except json.JSONDecodeError: break  # A writer may still be completing its last line.
    return result


def wait_phase(directory, pid, phase, previous=0):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        matches = [e for e in events(directory, pid) if e["phase"] == phase]
        if len(matches) > previous: return matches[-1]
        time.sleep(0.002)
    raise AssertionError(f"missing worker profile phase {pid}: {phase}")


def result_hash(rows):
    assert len(rows) == K
    assert [row["id"] for row in rows] == [f"bench:{i:08}" for i in range(K)]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def run_case(root, path, directory, mode):
    os.environ["STAR_VECTOR_TEK9_WORKER"] = str(root / "bin/star-vector-tek9-worker")
    if mode == "profile": os.environ["SV_PROFILE_DIR"] = str(directory)
    else: os.environ.pop("SV_PROFILE_DIR", None)
    client = Client(root / "lib/libstar_vector.so")
    handles = []
    def open_handle():
        before = children()
        handle = client.open(path, DIMENSIONS)
        pid, = children() - before
        handles.append((handle, pid))
        return handle, pid
    def search(handle): return result_hash(client.search(handle, 1, K, QUERY))
    try:
        initial = capture([])
        start = time.perf_counter(); handle, pid = open_handle()
        reopen = time.perf_counter() - start
        if mode == "timing":
            digest = search(handle)  # Warmup; no sampling, forced GC or profiling hooks.
            times = []
            for _ in range(3):
                start = time.perf_counter(); assert search(handle) == digest
                times.append(time.perf_counter() - start)
            return dict(seconds=times, median_seconds=statistics.median(times), result_hash=digest,
                        rows_per_second=COUNT / statistics.median(times), reopen_seconds=reopen)

        def collect(handle, pid):
            previous = sum(e["phase"] == "after_full_gc" for e in events(directory, pid))
            (directory / f"{pid}.collect").touch()
            assert client.lib.sv_len(handle) == COUNT
            event = wait_phase(directory, pid, "after_full_gc", previous)
            return dict(heap=event, processes=capture([p for _, p in handles]))

        def profiled_search(handle, pid):
            previous = sum(e["phase"] == "scan_end" for e in events(directory, pid))
            digest, measured_result = measured([pid], lambda: search(handle))
            end = wait_phase(directory, pid, "scan_end", previous)
            start = [e for e in events(directory, pid) if e["phase"] == "scan_start"][-1]
            assert end["rows"] == COUNT
            measured_result.update(result_hash=digest, heap=end,
                allocated_bytes=end["allocated_bytes"] - start["allocated_bytes"],
                gc_count=end["gc_count"] - start["gc_count"],
                gc_cpu_seconds=end["gc_cpu_seconds"] - start["gc_cpu_seconds"])
            return measured_result

        wait_phase(directory, pid, "open")
        opened = capture([pid])
        repeated = [profiled_search(handle, pid) for _ in range(3)]
        after_gc = collect(handle, pid)
        client.check(client.lib.sv_close(handle)); handles.remove((handle, pid))
        closed = capture([])
        start = time.perf_counter(); handle, pid = open_handle()
        second_reopen = time.perf_counter() - start
        reopened_search = profiled_search(handle, pid)
        reopened_gc = collect(handle, pid)

        for _ in range(2): open_handle()
        multi_open = capture([p for _, p in handles])
        warm = [profiled_search(h, p) for h, p in handles]
        multi_warm = capture([p for _, p in handles])
        previous = {p: sum(e["phase"] == "scan_end" for e in events(directory, p)) for _, p in handles}
        def simultaneous():
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                return list(pool.map(lambda hp: search(hp[0]), handles))
        digests, concurrent_result = measured([p for _, p in handles], simultaneous)
        concurrent_result["heap"] = {str(p): wait_phase(directory, p, "scan_end", previous[p]) for _, p in handles}
        concurrent_result["rows_per_second"] = COUNT * len(handles) / concurrent_result["seconds"]
        assert len(set(digests)) == 1
        collected = [collect(h, p)["heap"] for h, p in handles]
        multi_gc = capture([p for _, p in handles])
        return dict(initial=initial, opened=opened, reopen_seconds=reopen, repeated=repeated,
                    after_full_gc=after_gc, closed=closed, second_reopen_seconds=second_reopen,
                    reopened_search=reopened_search, reopened_full_gc=reopened_gc,
                    multiple_handles=dict(opened=multi_open, warm_searches=warm, warm=multi_warm,
                        concurrent=concurrent_result, after_full_gc=multi_gc, live_heaps=collected),
                    worker_events={str(p): events(directory, p) for _, p in handles})
    finally:
        for handle, _ in handles: client.check(client.lib.sv_close(handle))


def populate(worker_path, path):
    env = os.environ.copy(); env.pop("SV_PROFILE_DIR", None)
    worker = subprocess.Popen([str(worker_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, env=env)
    def request(value):
        worker.stdin.write(json.dumps(value) + "\n"); worker.stdin.flush()
        response = json.loads(worker.stdout.readline()); assert response["ok"], response
    try:
        request(dict(op="open", path=str(path), dimensions=DIMENSIONS, source="memory-profile", total=COUNT))
        for offset in range(0, COUNT, 128):
            batch = []
            for i in range(offset, min(offset + 128, COUNT)):
                doc = document(f"bench:{i:08}"); doc["rawContent"] = "x" * 1024
                batch.append(dict(id=doc["id"], record=json.dumps(dict(document=doc,
                    vector=[1.0, float(i % 7)] + [0.0] * (DIMENSIONS - 2)))))
            request(dict(op="migrate-batch", expected=offset, records=batch))
        request(dict(op="publish")); request(dict(op="close")); worker.wait(timeout=15)
    finally:
        if worker.poll() is None: worker.kill(); worker.wait()


def compare(before, after, output):
    with tempfile.TemporaryDirectory(prefix="sv-memory-") as temporary:
        temporary = Path(temporary); path = temporary / "store"
        populate(before / "bin/star-vector-tek9-worker", path)
        result = dict(baseline_revision="0247e40644d3277cf1c4f61337e449f278693688",
                      records=COUNT, dimensions=DIMENSIONS, document_payload_bytes=1024, k=K,
                      store_bytes=(path / "data.mdb").stat().st_size, variants={})
        def case(root, label, mode):
            directory = temporary / label; directory.mkdir()
            data = subprocess.check_output([sys.executable, __file__, "--case", str(root),
                str(path), str(directory), mode], text=True, timeout=180)
            return json.loads(data)
        for label, root in (("baseline", before), ("candidate", after)):
            result["variants"][label] = dict(profile=case(root, label, "profile"), timing=[])
        # ABBA timing order, separate fresh parent processes, no sampler or GC hooks.
        for i, (label, root) in enumerate((("baseline", before), ("candidate", after),
                                         ("candidate", after), ("baseline", before))):
            result["variants"][label]["timing"].append(case(root, f"timing-{i}", "timing"))
        hashes = {entry["result_hash"] for variant in result["variants"].values() for entry in variant["timing"]}
        assert len(hashes) == 1
        result["notes"] = [
            "Synthetic sparse vectors and tied dot-product results; page cache is warm, not a cold disk benchmark.",
            "RSS/PSS/anonymous peaks are sampled every 10 ms; VmHWM is the kernel's process-lifetime RSS high-water mark.",
            "Mapping categories use path names; Anonymous within worker_image includes copy-on-write pages, not file-backed memory.",
            "Dynamic-space occupancy includes garbage; only after_full_gc reports an explicit full-GC live-heap estimate.",
            "Dynamic peak sampled before/after every 128th callback; callback allocation counters may reflect SBCL allocation-region granularity.",
            "Callback allocation excludes producer cursor/Conspack decoding; total scan allocation includes both and small profiling overhead.",
            "Forced GC occurs only at labeled diagnostic boundaries, not between the three repeated searches or during uninstrumented timings.",
            "Parent metrics include the Python driver; summed RSS double-counts shared pages, while PSS apportions them.",
            "Timing images contain the same dormant profiling code, but instrumentation is inactive; the normal distributed worker excludes it."
        ]
        Path(output).write_text(json.dumps(result, indent=2) + "\n")
        summary = {}
        for label, variant in result["variants"].items():
            profile = variant["profile"]
            first = profile["repeated"][0]
            worker_pid = next(iter(first["steady"]["workers"]))
            summary[label] = dict(first_search_seconds=first["seconds"], allocated_bytes=first["allocated_bytes"],
                callback_allocated_bytes=first["heap"]["callback_allocated_bytes"],
                sampled_dynamic_peak_bytes=first["heap"]["sampled_dynamic_peak_bytes"],
                worker_peak=first["sampled_peaks"][worker_pid],
                live_heap_bytes=profile["after_full_gc"]["heap"]["dynamic_used_bytes"],
                repeated_worker_steady=[r["steady"]["workers"] for r in profile["repeated"]],
                multi_natural=profile["multiple_handles"]["warm"]["sum_process_metrics"],
                multi_collected=profile["multiple_handles"]["after_full_gc"]["sum_process_metrics"],
                timing_seconds=[s for run in variant["timing"] for s in run["seconds"]])
        print("MEMORY_PROFILE_SUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    if sys.argv[1] == "--case":
        print(json.dumps(run_case(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])))
    else:
        compare(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
