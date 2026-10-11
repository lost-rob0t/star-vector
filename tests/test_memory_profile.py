import unittest

from benchmarks.memory_profile import measured


class MemoryProfileTests(unittest.TestCase):
    def test_sampling_preserves_the_operations_result(self):
        expected = ["first-handle-digest", "second-handle-digest"]
        actual, measurements = measured([], lambda: expected)
        self.assertIs(actual, expected)
        self.assertGreater(measurements["steady"]["parent"]["Rss_kib"], 0)
