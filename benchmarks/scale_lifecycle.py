"""Real database lifecycle experiments; process reopen is explicitly not OS cold."""
import concurrent.futures
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np
from scale_common import metadata, percentile, recall


def lifecycle(backend, x, queries, truth):
    name = type(backend).__name__
    result = dict(true_cold_cache=dict(status="not executed", reason="No verified OS page-cache eviction; reopen retains cache"))
    if name == "Faiss" or (name == "StarVector" and not backend.path):
        return result | dict(status="unsupported for this lifecycle experiment")
    start = time.perf_counter()
    if name == "StarVector":
        backend.client.check(backend.client.lib.sv_close(backend.handle))
        backend.handle = backend.client.open(backend.path, backend.dimensions)
        cache = "new worker process, warm OS cache"
    elif name == "SQLiteVec":
        backend.db.close(); backend.db = backend.connect()
        cache = "new SQLite connection, same process and warm OS cache"
    else:
        if backend.process is None:
            return result | dict(status="server restart requires an owned child")
        backend.process.terminate(); backend.process.wait(timeout=20)
        backend.process = subprocess.Popen(**backend.launch)
        deadline = time.monotonic()+30
        while True:
            try:
                status = backend.call("GET", "/collections/"+backend.name)
                if status["result"]["points_count"] == len(x):
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("Qdrant process restart")
            time.sleep(0.1)
        cache = "new Qdrant process, warm OS cache"
    reopen = time.perf_counter()-start
    start = time.perf_counter(); found = backend.query(queries[64]); elapsed = time.perf_counter()-start
    result["reopen"] = dict(open_seconds=reopen, first_query_seconds=elapsed, cache=cache,
                            **recall(found, truth["ids_1000"][64], truth["scores_1000"][64], x, queries[64]))

    if name in ("StarVector", "SQLiteVec"):
        # Each thread owns an independent handle/connection. Record actual
        # end-to-end latency, including contention and per-worker validation.
        def read_batch(start_id):
            if name == "StarVector":
                handle = backend.client.open(backend.path, backend.dimensions)
            else:
                handle = backend.connect()
            times, scores = [], []
            try:
                for i in range(start_id, start_id+3):
                    start = time.perf_counter()
                    if name == "StarVector":
                        rows = backend.client.search(handle, 0, 10, queries[i])
                        found = [int(row["id"].split(":")[1]) for row in rows]
                    else:
                        found = backend.query(queries[i], db=handle)
                    times.append(time.perf_counter()-start)
                    scores.append(recall(found, truth["ids_1000"][i], truth["scores_1000"][i], x, queries[i])["id_recall"])
            finally:
                if name == "StarVector":
                    backend.client.check(backend.client.lib.sv_close(handle))
                else:
                    handle.close()
            return times, scores
        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(read_batch, (64, 67, 70, 73)))
        total = time.perf_counter()-start
        result["four_readers"] = dict(clients=4, wall_seconds=total, qps=12/total,
                                       **percentile([v for row in batches for v in row[0]]),
                                       id_recall=float(np.mean([v for row in batches for v in row[1]])),
                                       note="Wall throughput includes handle startup; query percentiles do not")

    if name == "StarVector":
        result["pinned_reader"] = pinned_reader(backend, x)
    else:
        result["pinned_reader"] = dict(status="Tek9-specific LMDB experiment; not applicable")
    return result


def pinned_reader(backend, x):
    worker = subprocess.Popen([os.environ["STAR_VECTOR_TEK9_WORKER"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def send(message):
        worker.stdin.write(json.dumps(message)+"\n"); worker.stdin.flush()
    def receive():
        reply = json.loads(worker.stdout.readline()); assert reply["ok"], reply; return reply
    file = Path(backend.path) / "data.mdb"
    n = min(1000, len(x))
    drained = False
    def overwrite(revision):
        times = []
        for i in range(n):
            doc = metadata(i, backend.dimensions); doc["rev"] = str(revision)
            start = time.perf_counter()
            backend.client.check(backend.client.lib.sv_upsert_document(backend.handle, json.dumps(doc).encode(), x[i].ctypes.data_as(C.POINTER(C.c_float)), backend.dimensions), backend.handle)
            times.append(time.perf_counter()-start)
        return times
    try:
        send(dict(op="open", path=backend.path, dimensions=backend.dimensions)); receive()
        before = file.stat().st_size; start = time.perf_counter()
        send(dict(op="scan")); first = receive()
        assert json.loads(first["record"])["document"]["rev"] == "1"
        # Stop consuming the pipe. The producer read transaction remains open.
        pinned = overwrite(2); during = file.stat().st_size
        count = 1
        while True:
            row = receive()
            if row.get("end"):
                break
            assert json.loads(row["record"])["document"]["rev"] == "1", "read snapshot mixed revisions"
            count += 1
        duration = time.perf_counter()-start
        assert count == len(x)
        drained = True
        released = overwrite(3); after = file.stat().st_size
        return dict(rows_overwritten=n, snapshot_records=count, snapshot_seconds=duration,
                    snapshot_revisions_consistent=True, before_bytes=before, pinned_bytes=during,
                    released_bytes=after, growth_while_pinned=during-before, growth_after_release=after-during,
                    pinned_writes=percentile(pinned), released_writes=percentile(released),
                    note="File size reflects allocated LMDB pages and need not shrink when readers close")
    finally:
        try:
            if drained:
                send(dict(op="close")); receive(); worker.wait(timeout=20)
        finally:
            if worker.poll() is None:
                worker.kill(); worker.wait(timeout=20)
