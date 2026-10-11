#!/usr/bin/env python3
"""Deterministic storage/embedding contract; fixture vectors are not a semantic model."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients/python"))
from star_vector_client import DocumentStore, Store, StarVectorError


class FixtureEncoder:
    profile = {"format": "star-vector/embedding/1", "dimensions": 4,
               "provider": "deterministic-test-fixture", "version": 1}
    vectors = {"alpha": [1, 2, 0, -1], "beta": [0, -1, 3, 2], "gamma": [2, 1, -2, 0]}

    def encode(self, text):
        return self.vectors[text]


def document(identifier, text="alpha"):
    return dict(id=identifier, dataset="synthetic-embedding-test", dtype="document", schemaVersion="0.10.1",
                rawContent=text, rev="1", provenance={"fixture": True}, extensions={"unrelated": {"keep": 1}})


class EmbeddingContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "store")
        self.encoder = FixtureEncoder()

    def tearDown(self):
        self.temp.cleanup()

    def test_metrics_update_reopen_and_metadata(self):
        original = document("a")
        with DocumentStore(self.path, self.encoder) as store:
            store.upsert(original)
            store.upsert(document("b", "beta"))
            store.upsert(document("c", "gamma"))
            store.upsert(document("a-tie"))
            self.assertEqual(original, document("a"))
            self.assertEqual(store.store.profile, self.encoder.profile)
        with DocumentStore(self.path, self.encoder) as store:
            q = self.encoder.encode("alpha")
            for metric in Store.METRICS:
                def score(v):
                    dot = sum(a*b for a, b in zip(q, v))
                    return {"dot": lambda: dot,
                            "cosine": lambda: dot / math.sqrt(sum(a*a for a in q)*sum(b*b for b in v)),
                            "euclidean": lambda: math.sqrt(sum((a-b)**2 for a, b in zip(q, v))),
                            "manhattan": lambda: sum(abs(a-b) for a, b in zip(q, v))}[metric]()
                expected = [(identifier, score(self.encoder.encode(text))) for identifier, text in
                            [("a", "alpha"), ("a-tie", "alpha"), ("b", "beta"), ("c", "gamma")]]
                expected.sort(key=lambda row: ((-row[1] if metric in ["cosine", "dot"] else row[1]), row[0]))
                rows = store.search("alpha", metric, 20)
                self.assertEqual([r["id"] for r in rows], [r[0] for r in expected])
                for row, (_, value) in zip(rows, expected): self.assertAlmostEqual(row["value"], value, places=12)
            changed = store.get("a")
            changed.update(rawContent="gamma", rev="2")
            store.upsert(changed)
        with DocumentStore(self.path, self.encoder) as store:
            actual = store.get("a")
            self.assertEqual(actual["provenance"], original["provenance"])
            self.assertEqual(actual["extensions"]["unrelated"], original["extensions"]["unrelated"])
            self.assertEqual((actual["rev"], actual["rawContent"]), ("2", "gamma"))
            self.assertEqual(store.search("gamma", "euclidean", 1)[0]["id"], "a")
            self.assertTrue(store.delete("a"))
            self.assertFalse(store.delete("a"))

    def test_profile_conflicts_raw_handle_and_no_implicit_adoption(self):
        with Store(self.path, 4) as stale:
            with DocumentStore(self.path, self.encoder) as store:
                with self.assertRaises(StarVectorError): stale.upsert(document("bad"), [1, 0, 0, 0])
                self.assertEqual(len(store.store), 0)
                store.upsert(document("a"))
                other = dict(self.encoder.profile, version=2)
                with self.assertRaises(StarVectorError): stale.bind_profile(other)
                with self.assertRaises(StarVectorError): stale.upsert(document("bad"), [1, 0, 0, 0], other)
                store.delete("a")
                with self.assertRaises(StarVectorError): stale.bind_profile(other)
                self.assertEqual(stale.profile, self.encoder.profile)
        raw = str(Path(self.temp.name) / "raw")
        with Store(raw, 4) as store: store.upsert(document("raw"), [1, 2, 3, 4])
        with self.assertRaises(StarVectorError): DocumentStore(raw, self.encoder)
        with Store(raw, 4) as store:
            self.assertIsNone(store.profile)
            self.assertEqual(store.get("raw"), document("raw"))

    def test_binding_race_has_one_winner(self):
        barrier = threading.Barrier(2)
        def bind(version):
            with Store(self.path, 4) as store:
                barrier.wait(timeout=10)
                try:
                    store.bind_profile(dict(self.encoder.profile, version=version))
                    return version
                except StarVectorError:
                    return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(bind, [1, 2]))
        winners = [x for x in results if x is not None]
        self.assertEqual(len(winners), 1)
        with Store(self.path, 4) as store: self.assertEqual(store.profile["version"], winners[0])

    def test_validation_and_transient_profile(self):
        with DocumentStore("", self.encoder) as store:
            for bad in [{}, document("bad", ""), dict(document("bad"), extensions=[]),
                        dict(document("bad"), extensions={"star-vector.embedding": "retain me"})]:
                with self.assertRaises((ValueError, StarVectorError)): store.upsert(bad)
            with self.assertRaises(StarVectorError): store.upsert(dict(document("bad"), inventedField=True))
            self.assertEqual(len(store.store), 0)
            store.upsert(document("a"))
            store.encoder = copy.deepcopy(self.encoder)
            store.encoder.profile = dict(self.encoder.profile, version=2)
            with self.assertRaises(ValueError): store.search("alpha")
        with self.assertRaises(ValueError): store.get("a")

    def test_acknowledged_write_survives_process_kill(self):
        child = subprocess.Popen([sys.executable, __file__, "--crash-child", self.path], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "committed")
            child.kill()
            child.communicate(timeout=10)
            self.assertEqual(child.returncode, -signal.SIGKILL)
            with DocumentStore(self.path, self.encoder) as store:
                self.assertEqual(store.get("crash")["rawContent"], "beta")
                self.assertEqual(store.search("beta", "euclidean", 1)[0]["value"], 0)
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=10)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--crash-child":
        import time
        store = DocumentStore(sys.argv[2], FixtureEncoder())
        store.upsert(document("crash", "beta"))
        print("committed", flush=True)
        time.sleep(30)
    else:
        unittest.main()
