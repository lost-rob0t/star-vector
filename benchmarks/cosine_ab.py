#!/usr/bin/env python3
"""ABBA process-isolated comparison of the original and norm-reuse exact scans."""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(key, "1")
import numpy as np
from scale_common import metadata, percentile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from storage_integration import Client


def child(library, root, count, store):
    x = np.load(root / "vectors.npy", mmap_mode="r")
    queries = np.load(root / "queries.npy", mmap_mode="r")
    truth = np.load(root / "truth.npz")["ids_1000"]
    client = Client(library); handle = client.open(store, x.shape[1])
    try:
        if not store or client.lib.sv_len(handle) == 0:
            for i, vector in enumerate(x):
                client.check(client.lib.sv_upsert_document(handle, json.dumps(metadata(i, x.shape[1])).encode(), vector.ctypes.data_as(C.POINTER(C.c_float)), x.shape[1]), handle)
        assert client.lib.sv_len(handle) == len(x)
        for q in queries[:3]:
            client.search(handle, 0, 10, q)
        times = []
        digest = hashlib.sha256()
        for i, q in enumerate(queries[64:64+count], 64):
            start = time.perf_counter(); rows = client.search(handle, 0, 10, q); times.append(time.perf_counter()-start)
            ids = [int(row["id"].split(":")[1]) for row in rows]
            assert ids == truth[i].tolist()
            digest.update(json.dumps([(row["id"], row["value"]) for row in rows]).encode())
        return dict(**percentile(times), result_sha256=digest.hexdigest(), records=len(x), dimensions=x.shape[1],
                    library_sha256=hashlib.sha256(Path(library).read_bytes()).hexdigest(),
                    persistence=bool(store),
                    note="C ABI, no memory sampler; all result IDs checked against independent oracle. Persistent passes reopen the same synthetic store with warm OS cache.")
    finally:
        client.check(client.lib.sv_close(handle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True); parser.add_argument("--candidate", required=True)
    parser.add_argument("--data", type=Path, required=True); parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--output", type=Path); parser.add_argument("--child", choices=["baseline", "candidate"])
    parser.add_argument("--persistent", action="store_true"); parser.add_argument("--store", default="")
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(getattr(args, args.child), args.data, args.queries, args.store))); return
    if args.output is None or args.output.exists():
        parser.error("Choose a new --output file")
    results = []
    with tempfile.TemporaryDirectory(prefix="sv-cosine-ab-") as root:
        for label in ("baseline", "candidate", "candidate", "baseline"):
            command = [sys.executable, str(Path(__file__).resolve()), "--baseline", args.baseline, "--candidate", args.candidate,
                       "--data", str(args.data), "--queries", str(args.queries), "--child", label,
                       "--store", str(Path(root)/"store") if args.persistent else ""]
            row = json.loads(subprocess.check_output(command, text=True))
            results.append(dict(label=label, **row)); print(json.dumps(results[-1]), flush=True)
    assert len({r["result_sha256"] for r in results}) == 1, "Exact result values or IDs changed"
    args.output.write_text(json.dumps(dict(order="ABBA", rows=results), indent=2)+"\n")


if __name__ == "__main__":
    main()
