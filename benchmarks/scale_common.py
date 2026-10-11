"""Shared synthetic inputs, independent blockwise oracle and Linux accounting."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import threading
import time

import numpy as np
import psutil

SEED = 1729
K = 10
FILTERS = (1000, 100, 10, 1, 0)


def metadata(i, dimensions):
    return dict(id=f"scale:{i:09}", dataset=f"synthetic-{i % 10}", dtype="document", schemaVersion="0.10.1",
                rev="1", rootId=f"scale:{i:09}", collectedAt=1700000000,
                validFrom=1700000000, provenance={"generator": "star-vector-scale-v1", "seed": SEED},
                extensions={"embedding": {"model": "synthetic-clusters", "version": "1", "dimensions": dimensions},
                            "benchmark": {"bucket": i % 1000, "tenant": i % 10}},
                rawContent="synthetic evidence " + "x" * 1005)


def percentile(seconds):
    values = np.asarray(seconds) * 1000
    return dict(samples=len(values), p50_ms=float(np.percentile(values, 50)),
                p95_ms=float(np.percentile(values, 95)), p99_ms=float(np.percentile(values, 99)),
                qps_serial=1 / float(np.mean(seconds)),
                p99_supported=len(values) >= 1000)


def environment():
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None
    return dict(platform=platform.platform(), python=platform.python_version(), cpu_count=os.cpu_count(),
                cpu_model=next((s.split(":", 1)[1].strip() for s in read("/proc/cpuinfo").splitlines() if s.startswith("model name")), None),
                cpu_max=read("/sys/fs/cgroup/cpu.max"), memory_max=read("/sys/fs/cgroup/memory.max"),
                disk_free_bytes=shutil.disk_usage(".").free,
                thread_environment={k: os.getenv(k) for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")})


def process_memory(pid):
    totals = dict(rss_kib=0, pss_kib=0, anonymous_kib=0, file_rss_kib=0)
    try:
        text = Path(f"/proc/{pid}/smaps").read_text()
        is_file = False
        for line in text.splitlines():
            if line and line[0] in "0123456789abcdef" and "-" in line.split()[0]:
                fields = line.split(None, 5)
                is_file = len(fields) == 6 and fields[5].startswith("/")
            elif line.startswith("Rss:"):
                value = int(line.split()[1]); totals["rss_kib"] += value
                if is_file:
                    totals["file_rss_kib"] += value
            elif line.startswith("Pss:"):
                totals["pss_kib"] += int(line.split()[1])
            elif line.startswith("Anonymous:"):
                totals["anonymous_kib"] += int(line.split()[1])
        return totals
    except (OSError, ProcessLookupError):
        return None


class Sampler:
    """Sample process tree plus optional external server. Peaks are sampled, not maxima proofs."""
    def __init__(self, extra_pids=()):
        self.extra_pids = extra_pids
        self.peaks = {}
        self.samples = 0
        self.io_max = {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def snapshot(self):
        pids = {os.getpid(), *self.extra_pids}
        pids.update(p.pid for p in psutil.Process().children(recursive=True))
        rows = {str(pid): process_memory(pid) for pid in pids}
        for pid in pids:
            try:
                counters = psutil.Process(pid).io_counters()._asdict()
                previous = self.io_max.setdefault(str(pid), {})
                for key, value in counters.items():
                    previous[key] = max(previous.get(key, 0), value)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        unavailable = [pid for pid, row in rows.items() if row is None]
        rows = {pid: row for pid, row in rows.items() if row is not None}
        totals = {key: sum(row[key] for row in rows.values()) for key in ("rss_kib", "pss_kib", "anonymous_kib", "file_rss_kib")}
        for key, value in totals.items():
            self.peaks[key] = max(self.peaks.get(key, 0), value)
        self.samples += 1
        return dict(total=totals, processes=rows, unavailable_pids=unavailable)

    def loop(self):
        while not self.stop.wait(0.1):
            self.snapshot()

    def __enter__(self):
        self.start = self.snapshot(); self.thread.start(); return self

    def __exit__(self, *_):
        self.stop.set(); self.thread.join(); self.end = self.snapshot()

    def result(self):
        return dict(interval_ms=100, samples=self.samples, peak_sum_kib=self.peaks,
                    start=self.start, end=self.end,
                    process_io_max=self.io_max,
                    note="RSS sums can double-count shared pages; PSS apportions them. File RSS includes COW pages; anonymous and file classifications can overlap.")


def normalized64(x):
    y = np.asarray(x, dtype=np.float64)
    norms = np.linalg.norm(y, axis=1, keepdims=True)
    if not np.isfinite(y).all() or np.any(norms == 0):
        raise ValueError("cosine normalization requires finite nonzero vectors")
    return y / norms


def oracle(x, queries, k=K, bucket_limit=1000):
    """Float64 cosine, bounded blocks, exact ID tie ordering, eligible corpus first."""
    q = normalized64(queries)
    ids = np.empty((len(q), 0), dtype=np.int64)
    scores = np.empty((len(q), 0), dtype=np.float64)
    for offset in range(0, len(x), 8192):
        block_ids = np.arange(offset, min(offset + 8192, len(x)))
        block_ids = block_ids[block_ids % 1000 < bucket_limit]
        if not len(block_ids):
            continue
        raw = np.asarray(x[block_ids], dtype=np.float64)
        if not np.isfinite(raw).all():
            raise ValueError("oracle corpus contains non-finite vectors")
        block_ids = block_ids[np.linalg.norm(raw, axis=1) != 0]
        if not len(block_ids):
            continue
        block = normalized64(x[block_ids])
        values = q @ block.T
        # argpartition may choose arbitrary boundary ties. Include all equal
        # boundary candidates, then lexsort by score and numeric ID.
        new_ids, new_scores = [], []
        for qi, row in enumerate(values):
            take = min(k, len(row))
            boundary = np.partition(row, len(row) - take)[len(row) - take]
            candidates = np.flatnonzero(row >= boundary)
            merged_ids = np.concatenate((ids[qi], block_ids[candidates]))
            merged_scores = np.concatenate((scores[qi], row[candidates]))
            order = np.lexsort((merged_ids, -merged_scores))[:k]
            new_ids.append(merged_ids[order]); new_scores.append(merged_scores[order])
        ids, scores = np.asarray(new_ids), np.asarray(new_scores)
    return ids, scores


def recall(found, truth_ids, truth_scores, x, query, bucket_limit=1000):
    found = list(map(int, found))
    eligible = sorted({i for i in found if 0 <= i < len(x) and i % 1000 < bucket_limit})
    if not len(truth_ids):
        return dict(id_recall=float(not found), tie_recall=float(not found), invalid=len(found))
    expected = set(map(int, truth_ids))
    values = normalized64(x[eligible]) @ normalized64(np.asarray(query)[None])[0] if eligible else []
    # Exact boundary ties only (small roundoff tolerance), not a broad ANN score tolerance.
    boundary = truth_scores[-1]
    tied = {i for i, value in zip(eligible, values) if value >= boundary - 1e-12}
    return dict(id_recall=len(set(eligible) & expected) / len(expected),
                tie_recall=min(len(tied), len(expected)) / len(expected),
                invalid=len(found) - len(eligible))


def generate(root, count, dimensions, queries=1000, distribution="clustered"):
    root = Path(root); root.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(SEED + dimensions)
    scales = np.random.default_rng(SEED + 1)
    centers = normalized64(rng.normal(size=(256, dimensions))).astype(np.float32)
    x = np.lib.format.open_memmap(root / "vectors.npy", mode="w+", dtype="float32", shape=(count, dimensions))
    for offset in range(0, count, 4096):
        n = min(4096, count - offset)
        noise = rng.normal(size=(n, dimensions)).astype(np.float32)
        if distribution == "clustered":
            noise = centers[np.arange(offset, offset+n) % len(centers)] + noise * (0.35 / np.sqrt(dimensions))
        x[offset:offset+n] = noise * scales.uniform(0.5, 2.0, size=(n, 1))
    x.flush()
    query_rng = np.random.default_rng(SEED + 90000 + dimensions)
    q = query_rng.normal(size=(queries + 64, dimensions)).astype(np.float32)
    if distribution == "clustered":
        q = centers[np.arange(len(q)) % len(centers)] + q * (0.35 / np.sqrt(dimensions))
    q = q.astype(np.float32)
    np.save(root / "queries.npy", q)
    truth = {}
    # Full test truth plus disjoint tuning set. Chunk queries to bound scratch.
    start = time.perf_counter()
    for limit in FILTERS:
        all_ids, all_scores = [], []
        for offset in range(0, len(q), 64):
            ids, scores = oracle(x, q[offset:offset+64], bucket_limit=limit)
            all_ids.append(ids); all_scores.append(scores)
        truth[f"ids_{limit}"] = np.concatenate(all_ids)
        truth[f"scores_{limit}"] = np.concatenate(all_scores)
    np.savez(root / "truth.npz", **truth)
    digest = hashlib.sha256()
    with (root / "vectors.npy").open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest = dict(seed=SEED, records=count, dimensions=dimensions, test_queries=queries,
                    tune_queries=64, distribution=distribution, vectors_sha256=digest.hexdigest(),
                    oracle_seconds=time.perf_counter() - start, metadata=metadata(0, dimensions),
                    logical_vector_bytes=count * dimensions * 4, numpy=np.__version__)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
