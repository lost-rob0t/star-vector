#!/usr/bin/env python3
"""Run one isolated, reproducible synthetic cell; JSON results never hide skips."""
import argparse
import concurrent.futures
import ctypes as C
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

# Set before importing numerical runtimes. One thread per library query permits
# a four-client experiment without accidentally requesting sixteen BLAS threads.
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(variable, "1")

import numpy as np
import psutil
from scale_common import K, FILTERS, Sampler, environment, generate, metadata, percentile, recall

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from storage_integration import Client


class Faiss:
    lane = "algorithm-library"
    durability = "rebuildable index file; not authoritative database"
    def __init__(self, args, x):
        import faiss
        self.faiss = faiss
        faiss.omp_set_num_threads(1)
        self.version = faiss.__version__
        self.path = args.output.with_suffix(".faiss")
        d = x.shape[1]
        self.name = args.method
        start = time.perf_counter()
        self.normalized = np.array(x, copy=True)
        faiss.normalize_L2(self.normalized)
        if args.method == "flat":
            self.index = faiss.IndexFlatIP(d)
        elif args.method == "hnsw":
            self.index = faiss.IndexHNSWFlat(d, 16, faiss.METRIC_INNER_PRODUCT)
            self.index.hnsw.efConstruction = 100
            self.index.hnsw.rng = faiss.RandomGenerator(1729)
        else:
            nlist = min(1024, max(16, int(np.sqrt(len(x)))))
            quantizer = faiss.IndexFlatIP(d)
            if args.method == "ivf":
                self.index = faiss.IndexIVFFlat(quantizer, d, nlist, faiss.METRIC_INNER_PRODUCT)
            elif args.method == "pq":
                self.index = faiss.IndexIVFPQ(quantizer, d, nlist, 16, 8, faiss.METRIC_INNER_PRODUCT)
                self.index.pq.cp.seed = 1729
            else:
                raise ValueError(args.method)
            self.index.cp.seed = 1729
            # Fixed, independent training subset of the base vectors, not test queries.
            train_ids = np.random.default_rng(1729).choice(len(x), min(len(x), 50000), replace=False)
            self.index.train(self.normalized[train_ids])
        self.index.add(self.normalized)
        self.build_seconds = time.perf_counter() - start
        faiss.write_index(self.index, str(self.path))
        self.storage_bytes = self.path.stat().st_size
        self.settings = dict(threads=1, normalized_float32=True, m=16 if args.method in ("hnsw", "pq") else None,
                             nlist=getattr(self.index, "nlist", None), ef_construction=100 if args.method == "hnsw" else None,
                             pq_rerank_candidates=100 if args.method == "pq" else None)
        self.supports_filters = args.method in ("ivf", "pq")
        self.x = x
        self.selectors = {limit: self.faiss.IDSelectorBatch(np.flatnonzero(np.arange(len(x)) % 1000 < limit).astype(np.int64))
                          for limit in FILTERS if limit != 1000} if self.supports_filters else {}
        self.settings["filter_selector"] = "prebuilt exact eligible ID sets; construction excluded from query latency"

    def query(self, q, bucket=1000):
        original_query = q
        q = np.array(q[None], dtype=np.float32, copy=True)
        self.faiss.normalize_L2(q)
        params = None
        if bucket != 1000:
            if not self.supports_filters:
                raise NotImplementedError("No native filter tested for this method")
            params = self.faiss.SearchParametersIVF(nprobe=self.index.nprobe, sel=self.selectors[bucket])
        count = 100 if self.name == "pq" else K
        _, ids = self.index.search(q, count, params=params)
        found = ids[0][ids[0] >= 0]
        if self.name == "pq" and len(found):
            # Exact float64 rerank over original vectors, still bounded by candidate count.
            vectors = np.asarray(self.x[found], dtype=np.float64)
            query = np.asarray(original_query, dtype=np.float64)
            scores = (vectors @ query) / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(query))
            found = found[np.lexsort((found, -scores))[:K]]
        return found.tolist()

    def tune(self, queries, truth, x):
        if self.name == "flat":
            return []
        choices = (32, 64, 128, 256) if self.name == "hnsw" else (1, 4, 16, 64)
        rows = []
        for value in choices:
            if self.name == "hnsw":
                self.index.hnsw.efSearch = value
            else:
                self.index.nprobe = min(value, self.index.nlist)
            scores, timings = [], []
            for i, q in enumerate(queries[:64]):
                start = time.perf_counter(); ids = self.query(q); timings.append(time.perf_counter()-start)
                scores.append(recall(ids, truth["ids_1000"][i], truth["scores_1000"][i], x, q)["id_recall"])
            rows.append(dict(value=value, id_recall=float(np.mean(scores)), **percentile(timings)))
            if rows[-1]["id_recall"] >= 0.95:
                break
        self.settings["ef_search" if self.name == "hnsw" else "nprobe"] = value
        return rows

    def close(self):
        self.path.unlink(missing_ok=True)


class SQLiteVec:
    lane = "embedded-database"
    durability = "SQLite WAL synchronous=FULL; 128-row ingest transactions"
    supports_filters = True
    def __init__(self, args, x):
        import sqlite_vec
        self.extension = sqlite_vec
        self.version = dict(extension=sqlite_vec.__version__, sqlite=sqlite3.sqlite_version)
        self.path = args.output.with_suffix(".sqlite")
        self.dimensions = x.shape[1]
        self.db = self.connect()
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(f"CREATE VIRTUAL TABLE vectors USING vec0(embedding float[{self.dimensions}] distance_metric=cosine, bucket integer, +document text)")
        start = time.perf_counter()
        for offset in range(0, len(x), 128):
            self.db.executemany("INSERT INTO vectors(rowid,embedding,bucket,document) VALUES(?,?,?,?)",
                                ((i, x[i].tobytes(), i % 1000, json.dumps(metadata(i, self.dimensions))) for i in range(offset, min(offset+128, len(x)))))
            self.db.commit()
        self.build_seconds = time.perf_counter() - start
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.storage_bytes = self.path.stat().st_size
        self.settings = dict(batch_rows=128, synchronous="FULL", journal="WAL", payload_returned=True)

    def connect(self):
        db = sqlite3.connect(str(self.path), check_same_thread=False)
        db.enable_load_extension(True); self.extension.load(db); db.enable_load_extension(False)
        return db

    def query(self, q, bucket=1000, db=None):
        rows = (db or self.db).execute("SELECT rowid,distance,document FROM vectors WHERE embedding MATCH ? AND k=? AND bucket < ? ORDER BY distance", (q.tobytes(), K, bucket)).fetchall()
        for row in rows:
            json.loads(row[2])
        return [row[0] for row in rows]

    def mutate(self, x):
        n = min(100, len(x)); start = time.perf_counter()
        for i in range(n):
            doc = metadata(i, self.dimensions); doc["rev"] = "2"
            self.db.execute("UPDATE vectors SET embedding=?,document=? WHERE rowid=?", (x[(i+1) % len(x)].tobytes(), json.dumps(doc), i))
            self.db.commit()
        update = time.perf_counter() - start; start = time.perf_counter()
        for i in range(n):
            self.db.execute("DELETE FROM vectors WHERE rowid=?", (i,)); self.db.commit()
        delete = time.perf_counter() - start
        self.db.close(); self.db = self.connect()
        assert self.db.execute("SELECT count(*) FROM vectors").fetchone()[0] == len(x) - n
        return dict(rows=n, update_seconds=update, delete_seconds=delete, reopened_count=len(x)-n,
                    bytes_after=self.path.stat().st_size)

    def close(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)


class Qdrant:
    lane = "http-database"
    durability = "wait=true acknowledged WAL operations; not assumed equivalent to LMDB full fsync"
    supports_filters = True
    def __init__(self, args, x):
        import requests
        self.session = requests.Session()
        self.base = args.qdrant.rstrip("/")
        self.process = None
        self.temporary = None
        if args.qdrant_binary:
            self.temporary = tempfile.TemporaryDirectory(prefix="sv-qdrant-", dir=args.output.parent)
            args.qdrant_storage = Path(self.temporary.name) / "storage"
            self.server_log = args.output.with_suffix(".server.log").open("w")
            env = os.environ | {"QDRANT__STORAGE__STORAGE_PATH": str(args.qdrant_storage),
                                "QDRANT__SERVICE__HTTP_PORT": "16333", "QDRANT__SERVICE__GRPC_PORT": "16334",
                                "QDRANT__STORAGE__PERFORMANCE__MAX_SEARCH_THREADS": "4", "QDRANT__TELEMETRY_DISABLED": "true"}
            self.launch = dict(args=[args.qdrant_binary], cwd=self.temporary.name, env=env, stdout=self.server_log, stderr=subprocess.STDOUT)
            self.process = subprocess.Popen(**self.launch)
            deadline = time.monotonic()+20
            while True:
                if self.process.poll() is not None:
                    raise RuntimeError("Qdrant child exited at startup; inspect server log")
                try:
                    self.call("GET", "")
                    break
                except requests.ConnectionError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)
        self.name = "sv_scale_" + str(os.getpid())
        self.dimensions = x.shape[1]
        self.version = self.call("GET", "")
        self.settings = dict(hnsw_m=16, ef_construct=100, ef_search=128, payload_returned=True,
                             vectors_on_disk=False, payload_on_disk=True, indexing_threshold_kib=1000,
                             transport="HTTP JSON loopback", server_cpu_quota=4)
        start = time.perf_counter()
        self.call("PUT", "/collections/"+self.name, dict(vectors=dict(size=self.dimensions, distance="Cosine", on_disk=False),
                  on_disk_payload=True, hnsw_config=dict(m=16, ef_construct=100), optimizers_config=dict(indexing_threshold=1000)))
        self.call("PUT", f"/collections/{self.name}/index?wait=true", dict(field_name="bucket", field_schema="integer"))
        for offset in range(0, len(x), 128):
            points = [dict(id=i, vector=x[i].tolist(), payload=dict(bucket=i % 1000, document=metadata(i, self.dimensions))) for i in range(offset, min(offset+128, len(x)))]
            self.call("PUT", f"/collections/{self.name}/points?wait=true", dict(points=points))
        self.ingest_seconds = time.perf_counter() - start
        deadline = time.monotonic()+300
        previous_indexed = -1
        stable = 0
        while True:
            status = self.call("GET", "/collections/"+self.name)["result"]
            indexed = status.get("indexed_vectors_count", 0)
            stable = stable + 1 if indexed == previous_indexed else 0
            previous_indexed = indexed
            # Small segments below the configured threshold may stay exact.
            # Report the actual indexed count instead of falsely calling all rows HNSW.
            if status["status"] == "green" and indexed > 0 and stable >= 10:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Qdrant index not ready: {status}")
            time.sleep(0.5)
        self.build_seconds = time.perf_counter() - start
        self.status = status
        self.storage_root = args.qdrant_storage / "collections" / self.name if args.qdrant_storage else None
        self.storage_bytes = sum(p.stat().st_size for p in self.storage_root.rglob("*") if p.is_file()) if self.storage_root else None
        self.storage_note = "Collection files including WAL; sparse logical file sizes, not physical device writes"

    def call(self, method, path, body=None):
        response = self.session.request(method, self.base+path, json=body, timeout=120)
        response.raise_for_status(); return response.json()

    def query(self, q, bucket=1000):
        body = dict(query=q.tolist(), limit=K, params=dict(hnsw_ef=128, exact=False), with_payload=True)
        if bucket != 1000:
            body["filter"] = dict(must=[dict(key="bucket", range=dict(lt=bucket))])
        points = self.call("POST", f"/collections/{self.name}/points/query", body)["result"]["points"]
        return [point["id"] for point in points]

    def mutate(self, x):
        n = min(100, len(x)); start = time.perf_counter()
        for i in range(n):
            doc = metadata(i, self.dimensions); doc["rev"] = "2"
            self.call("PUT", f"/collections/{self.name}/points?wait=true", dict(points=[dict(id=i, vector=x[(i+1) % len(x)].tolist(), payload=dict(bucket=i%1000, document=doc))]))
        update = time.perf_counter()-start; start = time.perf_counter()
        for i in range(n):
            self.call("POST", f"/collections/{self.name}/points/delete?wait=true", dict(points=[i]))
        delete = time.perf_counter()-start
        count = self.call("POST", f"/collections/{self.name}/points/count", dict(exact=True))["result"]["count"]
        assert count == len(x)-n
        return dict(rows=n, update_seconds=update, delete_seconds=delete, count=count,
                    reopen="not executed; collection count is not a server restart test")

    def close(self):
        try:
            if hasattr(self, "name"):
                self.call("DELETE", "/collections/"+self.name)
        finally:
            self.session.close()
            if self.process:
                self.process.terminate(); self.process.wait(timeout=20)
                self.server_log.close(); self.temporary.cleanup()


class StarVector:
    lane = "c-abi-transient"  # Persistent mode overrides this explicitly.
    supports_filters = False
    version = "source recorded by runner"
    def __init__(self, args, x):
        self.client = Client(args.library)
        self.path = str(args.output.with_suffix(".tek9")) if args.method == "tek9" else ""
        self.durability = "Tek9 LMDB full; single-row C ABI ingress" if self.path else "none; transient memory only"
        self.lane = "c-abi-tek9-database" if self.path else "c-abi-transient"
        self.dimensions = x.shape[1]
        start = time.perf_counter(); self.handle = self.client.open(self.path, self.dimensions)
        self.open_seconds = time.perf_counter()-start
        self.settings = dict(payload_returned=True, ingress_schema_validated=True, transport="C ABI plus worker JSON pipe" if self.path else "C ABI", batch_rows=1)
        start = time.perf_counter()
        for i, vector in enumerate(x):
            self.client.check(self.client.lib.sv_upsert_document(self.handle, json.dumps(metadata(i, self.dimensions)).encode(), vector.ctypes.data_as(C.POINTER(C.c_float)), self.dimensions), self.handle)
        self.build_seconds = time.perf_counter()-start
        self.storage_bytes = sum(p.stat().st_size for p in Path(self.path).rglob("*") if p.is_file()) if self.path else None

    def query(self, q, bucket=1000):
        assert bucket == 1000
        return [int(row["id"].split(":")[1]) for row in self.client.search(self.handle, 0, K, q)]

    def mutate(self, x):
        n = min(100, len(x)); start = time.perf_counter()
        for i in range(n):
            doc = metadata(i, self.dimensions); doc["rev"] = "2"
            vector = x[(i+1) % len(x)]
            self.client.check(self.client.lib.sv_upsert_document(self.handle, json.dumps(doc).encode(), vector.ctypes.data_as(C.POINTER(C.c_float)), self.dimensions), self.handle)
        update = time.perf_counter()-start; start = time.perf_counter()
        delete = self.client.lib.sv_delete_document
        delete.argtypes = [C.c_void_p, C.c_char_p, C.POINTER(C.c_int)]; delete.restype = C.c_int
        for i in range(n):
            deleted = C.c_int(); self.client.check(delete(self.handle, f"scale:{i:09}".encode(), C.byref(deleted)), self.handle)
            assert deleted.value
        elapsed = time.perf_counter()-start
        assert self.client.lib.sv_len(self.handle) == len(x)-n
        return dict(rows=n, update_seconds=update, delete_seconds=elapsed, count=len(x)-n)

    def close(self):
        if hasattr(self, "handle"):
            self.client.check(self.client.lib.sv_close(self.handle))
        # Persisted synthetic paths are retained for explicit reopen/concurrency experiments.


def run(args):
    root = args.data
    manifest = json.loads((root / "manifest.json").read_text())
    x = np.load(root / "vectors.npy", mmap_mode="r")
    queries = np.load(root / "queries.npy", mmap_mode="r")
    assert x.dtype == queries.dtype == np.float32, "All engines must receive identical float32 inputs"
    truth = np.load(root / "truth.npz")
    result = dict(manifest=manifest, environment=environment(), backend=args.backend, method=args.method,
                  cache="warm after disjoint tuning/warmup; no claim of OS cold cache", filters=[])
    result["source_files_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in Path(__file__).parent.glob("scale*.py")}
    result["query_sha256"] = hashlib.sha256((root / "queries.npy").read_bytes()).hexdigest()
    result["library_sha256"] = hashlib.sha256(Path(args.library).read_bytes()).hexdigest() if args.library else None
    result["qdrant_binary_sha256"] = hashlib.sha256(Path(args.qdrant_binary).read_bytes()).hexdigest() if args.qdrant_binary else None
    backend = None
    failure = None
    before_io = psutil.Process().io_counters()._asdict()
    with Sampler([args.server_pid] if args.server_pid else []) as sampler:
        try:
            constructor = {"faiss": Faiss, "sqlite": SQLiteVec, "qdrant": Qdrant, "starvector": StarVector}[args.backend]
            backend = constructor.__new__(constructor)
            constructor.__init__(backend, args, x)
            result.update(lane=backend.lane, version=backend.version, durability=backend.durability,
                          build_seconds=backend.build_seconds, disk_bytes=backend.storage_bytes,
                          settings=backend.settings, memory_after_build=sampler.snapshot())
            if isinstance(backend, Faiss):
                result["tuning"] = backend.tune(queries, truth, x)
            else:
                for q in queries[:min(3, args.queries)]:
                    backend.query(q)
            if isinstance(backend, Qdrant):
                result.update(ingest_seconds=backend.ingest_seconds, index_status=backend.status)
            for bucket in FILTERS:
                if bucket != 1000 and not backend.supports_filters:
                    result["filters"].append(dict(bucket_limit=bucket, status="unsupported", reason="No eligible-corpus filter API exercised; global top-k postfilter deliberately excluded"))
                    continue
                # Filter subsets use 100 samples; unfiltered fast methods use >=1000.
                count = min(args.queries, len(queries)-64, 100 if bucket != 1000 else args.queries)
                timings, scores = [], []
                for i, q in enumerate(queries[64:64+count], 64):
                    start = time.perf_counter(); found = backend.query(q, bucket); timings.append(time.perf_counter()-start)
                    scores.append(recall(found, truth[f"ids_{bucket}"][i], truth[f"scores_{bucket}"][i], x, q, bucket))
                result["filters"].append(dict(bucket_limit=bucket, eligible_records=int(np.count_nonzero(np.arange(len(x)) % 1000 < bucket)),
                                               **percentile(timings), id_recall=float(np.mean([s["id_recall"] for s in scores])),
                                               tie_recall=float(np.mean([s["tie_recall"] for s in scores])), invalid_results=sum(s["invalid"] for s in scores)))
            # Independent library queries/HTTP requests; single StarVector handle is intentionally serialized.
            if args.backend in ("faiss", "qdrant"):
                subset = queries[64:64+min(args.queries, 200)]
                def one(q):
                    if isinstance(backend, Faiss):
                        backend.faiss.omp_set_num_threads(1)
                    start = time.perf_counter(); backend.query(q); return time.perf_counter()-start
                start = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    durations = list(pool.map(one, subset))
                result["concurrency"] = dict(clients=4, qps=len(durations)/(time.perf_counter()-start), **percentile(durations))
            else:
                result["concurrency"] = dict(status="separate independent-handle experiment required")
            if args.lifecycle:
                from scale_lifecycle import lifecycle
                result["lifecycle"] = lifecycle(backend, x, queries, truth)
            if hasattr(backend, "mutate"):
                result["mutations"] = backend.mutate(x)
            else:
                result["mutations"] = dict(status="not measured; static index experiment, not a mutable database")
            result["status"] = "completed"
        except Exception as error:
            result.update(status="failed", error=repr(error))
            failure = error
        finally:
            result["process_io_before"] = before_io
            result["process_io_after"] = psutil.Process().io_counters()._asdict()
            result["memory_before_close"] = sampler.snapshot()
            if backend is not None:
                try:
                    backend.close()
                except Exception as error:
                    result["cleanup_error"] = repr(error)
                    if failure is None:
                        failure = error
                        result.update(status="failed", error=repr(error))
    result["memory"] = sampler.result()
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({key: result.get(key) for key in ("status", "backend", "method", "build_seconds", "disk_bytes", "filters", "memory")}))
    if failure is not None:
        raise failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--data", type=Path, required=True); gen.add_argument("--records", type=int, required=True)
    gen.add_argument("--dimensions", type=int, required=True); gen.add_argument("--queries", type=int, default=1000)
    gen.add_argument("--distribution", choices=["clustered", "uniform"], default="clustered")
    bench = sub.add_parser("run")
    bench.add_argument("--data", type=Path, required=True); bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--backend", choices=["faiss", "sqlite", "qdrant", "starvector"], required=True)
    bench.add_argument("--method", default="flat"); bench.add_argument("--queries", type=int, default=1000)
    bench.add_argument("--library"); bench.add_argument("--qdrant", default="http://127.0.0.1:16333")
    bench.add_argument("--server-pid", type=int)
    bench.add_argument("--qdrant-storage", type=Path)
    bench.add_argument("--qdrant-binary", type=str, help="Start a pinned local server as a measured child; loopback port 16333 must be free")
    bench.add_argument("--lifecycle", action="store_true", help="Run representative independent-handle concurrency and reopen experiments")
    args = parser.parse_args()
    if args.command == "generate":
        print(json.dumps(generate(args.data, args.records, args.dimensions, args.queries, args.distribution)))
    else:
        if args.output.exists():
            parser.error("output already exists; keep prior evidence and choose a new path")
        try:
            run(args)
        except Exception as error:
            if not args.output.exists():
                args.output.write_text(json.dumps(dict(status="failed", backend=args.backend, method=args.method, error=repr(error)), indent=2)+"\n")
            raise


if __name__ == "__main__":
    main()
