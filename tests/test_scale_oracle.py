import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

HAS_NUMPY = importlib.util.find_spec("numpy") is not None and importlib.util.find_spec("psutil") is not None
if HAS_NUMPY:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
    from scale_common import generate, oracle, recall


@unittest.skipUnless(HAS_NUMPY, "optional scale benchmark dependencies not installed")
class OracleTests(unittest.TestCase):
    def test_float32_inputs_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as root:
            paths = [Path(root) / str(i) for i in range(2)]
            manifests = [generate(p, 48, 8, queries=3) for p in paths]
            self.assertEqual(manifests[0]["vectors_sha256"], manifests[1]["vectors_sha256"])
            for p in paths:
                self.assertEqual(np.load(p / "queries.npy").dtype, np.float32)
                self.assertEqual(np.load(p / "vectors.npy").dtype, np.float32)
            np.testing.assert_array_equal(np.load(paths[0] / "queries.npy"), np.load(paths[1] / "queries.npy"))

    def test_eligible_corpus_ties_and_empty(self):
        x = np.tile(np.array([[1, 0]], dtype=np.float32), (1100, 1))
        q = np.array([[2, 0]], dtype=np.float32)
        ids, scores = oracle(x, q, k=10, bucket_limit=1)
        self.assertEqual(ids.tolist(), [[0, 1000]])
        self.assertEqual(scores.tolist(), [[1, 1]])
        self.assertEqual(oracle(x, q, bucket_limit=0)[0].shape, (1, 0))
        r = recall([1, 1000], ids[0], scores[0], x, q[0], bucket_limit=1)
        self.assertEqual(r["invalid"], 1)
        self.assertEqual(r["id_recall"], 0.5)

    def test_tie_recall_does_not_require_arbitrary_boundary_ids(self):
        x = np.array([[1, 0], [2, 0], [3, 0]], dtype=np.float32)
        q = x[:1]
        ids, scores = oracle(x, q, k=1)
        self.assertEqual(ids.tolist(), [[0]])
        r = recall([2], ids[0], scores[0], x, q[0])
        self.assertEqual(r["id_recall"], 0)
        self.assertEqual(r["tie_recall"], 1)
        self.assertEqual(recall([2, 2], ids[0], scores[0], x, q[0])["invalid"], 1)


if __name__ == "__main__":
    unittest.main()
