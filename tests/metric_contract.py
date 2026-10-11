#!/usr/bin/env python3
"""Independent scalar oracle against the actual C ABI, transient and reopened Tek9."""
import ctypes as C
import json
import math
from pathlib import Path
import random
import sys
import tempfile

from storage_integration import Client, document


def f32(value):
    return C.c_float(value).value


def reference(rows, query, metric, k):
    """No engine code, normalization cache, NumPy or approximate index is reused."""
    query = list(map(f32, query))
    values = []
    qnorm = math.sqrt(sum(v * v for v in query))
    for identifier, vector in rows.items():
        vector = list(map(f32, vector))
        if metric == 0:
            norm = math.sqrt(sum(v * v for v in vector))
            if not norm:
                continue
            score = sum(a * b for a, b in zip(query, vector)) / (qnorm * norm)
        elif metric == 1:
            score = sum(a * b for a, b in zip(query, vector))
        elif metric == 2:
            score = math.sqrt(sum((a - b) ** 2 for a, b in zip(query, vector)))
        else:
            score = sum(abs(a - b) for a, b in zip(query, vector))
        values.append((identifier, score))
    return sorted(values, key=lambda row: ((-row[1] if metric < 2 else row[1]), row[0]))[:k]


def run(library, persistent=True):
    client = Client(library)
    rng = random.Random(1729)
    checks = 0
    with tempfile.TemporaryDirectory(prefix="sv-metric-contract-") as root:
        for dim in (3, 128, 384, 768, 1536):
            rows = {f"metric:{i:04}": [f32(rng.uniform(-3, 3)) for _ in range(dim)] for i in range(24)}
            # Deliberate exact ties, antipodes, zero and non-unit vectors.
            rows.update({"tie:a": [2.0] + [0.0] * (dim - 1),
                         "tie:b": [2.0] + [0.0] * (dim - 1),
                         "zero": [0.0] * dim,
                         "negative": [-2.0] + [0.0] * (dim - 1)})
            queries = [[1.0] + [0.0] * (dim - 1),
                       [f32(rng.uniform(-2, 2)) for _ in range(dim)]]
            for mode in (["transient", "tek9"] if persistent else ["transient"]):
                path = str(Path(root) / str(dim)) if mode == "tek9" else ""
                handle = client.open(path, dim)
                try:
                    for identifier, vector in rows.items():
                        client.check(client.put(handle, identifier, vector), handle)
                    if path:
                        client.check(client.lib.sv_close(handle)); handle = client.open(path, dim)
                    for query in queries:
                        for metric in range(4):
                            for k in (0, 1, 10, len(rows) + 1):
                                got = client.search(handle, metric, k, query)
                                want = reference(rows, query, metric, k)
                                assert [r["id"] for r in got] == [r[0] for r in want], (dim, mode, metric, k)
                                for actual, expected in zip(got, want):
                                    assert math.isclose(actual["value"], expected[1], rel_tol=3e-13, abs_tol=3e-13), (actual, expected)
                                checks += 1
                    for invalid in ([0.0] * dim, [float("nan")] * dim, [float("inf")] * dim, [1.0] * (dim - 1)):
                        out = C.c_void_p()
                        code = client.lib.sv_search_json(handle, (C.c_float * len(invalid))(*invalid), len(invalid), 0, 10, C.byref(out))
                        assert code != 0 and not out.value
                        checks += 1
                    for invalid in ([float("nan")] * dim, [float("inf")] * dim, [1.0] * (dim - 1)):
                        assert client.put(handle, "invalid", invalid) != 0
                        checks += 1
                    # Cosine is invariant to positive query scaling, unlike dot.
                    normal = client.search(handle, 0, 10, queries[0])
                    scaled = client.search(handle, 0, 10, [x * 7 for x in queries[0]])
                    assert [r["id"] for r in normal] == [r["id"] for r in scaled]
                    assert all(math.isclose(a["value"], b["value"], abs_tol=3e-13) for a, b in zip(normal, scaled))
                    checks += 1
                finally:
                    client.check(client.lib.sv_close(handle))
    result = dict(checks=checks, dimensions=[3, 128, 384, 768, 1536], persistent=persistent, metrics=["cosine", "dot", "euclidean", "manhattan"])
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    run(sys.argv[1], "--transient-only" not in sys.argv[2:])
