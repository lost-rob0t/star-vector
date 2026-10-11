#!/usr/bin/env python3
"""Separate real-model proof on synthetic prose, plus local language interoperability."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients/python"))
from star_vector_client import DocumentStore, SpacyEncoder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nim-client", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    encoder = SpacyEncoder()
    report = {"kind": "real-pinned-model-synthetic-prose-proof", "profile": encoder.profile,
              "platform": platform.platform(), "python": platform.python_version(),
              "model_load_seconds": time.perf_counter()-started,
              "dependencies": {x.metadata["Name"]: x.version for x in importlib.metadata.distributions()},
              "limits": ["English static word vectors; no contextual encoder or retrieval benchmark claim",
                         "Convenience clients start a Python encoder per call; Python library reuses it",
                         "No remote service or billion-scale text execution"]}
    texts = ["A cat sleeps on the sofa.", "The database commits durable transactions.",
             "A rocket travels through outer space.", "The chef cooks pasta in the kitchen."]
    query = "A kitten rests on a couch."
    vectors = [encoder.encode(t) for t in texts]
    q = encoder.encode(query)
    report["vector_sha256"] = hashlib.sha256(json.dumps(vectors).encode()).hexdigest()
    report["texts"], report["query"] = texts, query
    with tempfile.TemporaryDirectory(prefix="star-vector-text-proof-") as temporary:
        path = str(Path(temporary) / "tek9")
        start = time.perf_counter()
        with DocumentStore(path, encoder) as store:
            for i, text in enumerate(texts):
                store.upsert(dict(id=f"synthetic-{i}", dataset="synthetic-real-model-proof", dtype="document",
                                  schemaVersion="0.10.1", rawContent=text, provenance={"fixture": True}))
            report["ingest_four_seconds"] = time.perf_counter()-start
            metrics = {}
            for metric in ["cosine", "dot", "euclidean", "manhattan"]:
                start = time.perf_counter()
                rows = store.search(query, metric, 4)
                seconds = time.perf_counter()-start
                def score(v):
                    if metric == "dot": return sum(a*b for a, b in zip(q, v))
                    if metric == "cosine": return sum(a*b for a, b in zip(q, v))/math.sqrt(sum(a*a for a in q)*sum(b*b for b in v))
                    if metric == "euclidean": return math.sqrt(sum((a-b)**2 for a, b in zip(q, v)))
                    return sum(abs(a-b) for a, b in zip(q, v))
                expected = [(f"synthetic-{i}", score(v)) for i, v in enumerate(vectors)]
                expected.sort(key=lambda x: ((-x[1] if metric in ["cosine", "dot"] else x[1]), x[0]))
                assert [r["id"] for r in rows] == [r[0] for r in expected]
                assert all(math.isclose(r["value"], e[1], rel_tol=1e-12, abs_tol=1e-12) for r, e in zip(rows, expected))
                assert rows[0]["id"] == "synthetic-0", (metric, rows)
                metrics[metric] = {"seconds": seconds, "ranking": [{k: r[k] for k in ["id", "value"]} for r in rows]}
            report["metrics"] = metrics
            store.upsert(dict(id="synthetic-shared", dataset="synthetic-real-model-proof", dtype="document",
                              schemaVersion="0.10.1", rawContent="A musician plays piano on stage.", rev="python",
                              provenance={"fixture": True}, extensions={"unrelated": {"keep": 1}}))
        with DocumentStore(path, SpacyEncoder()) as reopened:
            assert reopened.search(query)[0]["id"] == "synthetic-0"
        report["reopen_new_encoder"] = "passed"
        runs = [([args.nim_client, path], "nim"), (["node", "tests/text_client.mjs", path], "javascript"),
                (["sbcl", "--noinform", "--non-interactive", "--no-userinit", "--no-sysinit", "--load", "tests/text_client.lisp"], "common_lisp")]
        report["clients"] = {}
        for command, language in runs:
            env = dict(os.environ, STAR_VECTOR_TEXT_TEST_STORE=path)
            completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=240)
            if completed.returncode:
                raise RuntimeError(f"{language} failed:\n{completed.stdout}\n{completed.stderr}")
            report["clients"][language] = {"status": "passed", "stdout": completed.stdout.strip().splitlines()[-1]}
        with DocumentStore(path, encoder) as store:
            actual = store.get("synthetic-shared")
            assert actual["rev"] == "lisp" and actual["provenance"] == {"fixture": True}
            assert actual["extensions"]["unrelated"] == {"keep": 1}
            assert store.search(actual["rawContent"], "euclidean", 1)[0]["value"] == 0
        report["clients"]["python"] = {"status": "passed", "final_revision": actual["rev"]}
        child = subprocess.Popen([sys.executable, __file__, "--crash-child", path],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert child.stdout.readline().strip() == "committed"
            child.kill()
            child.communicate(timeout=10)
            assert child.returncode == -signal.SIGKILL
            with DocumentStore(path, encoder) as store:
                text = store.get("synthetic-shared")["rawContent"]
                assert store.get("synthetic-shared")["rev"] == "crash"
                assert store.search(text, "euclidean", 1)[0]["value"] == 0
            report["real_model_acknowledged_write_sigkill_reopen"] = "passed"
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=10)
        for invalid in ["", " "*10, "a"*1000001, "qzxwvvqqzzxx"]:
            try: encoder.encode(invalid)
            except ValueError: pass
            else: raise AssertionError("invalid/unknown text accepted")
        report["input_rejections"] = "passed"
    report["total_seconds"] = time.perf_counter()-started
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print("Real-model proof and Python/Nim/JavaScript/Common Lisp shared-store clients passed")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--crash-child":
        store = DocumentStore(sys.argv[2])
        doc = store.get("synthetic-shared")
        doc.update(rev="crash", rawContent="A painter mixes bright colors on a canvas.")
        store.upsert(doc)
        print("committed", flush=True)
        time.sleep(30)
    else:
        main()
