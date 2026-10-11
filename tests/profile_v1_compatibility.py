#!/usr/bin/env python3
"""Exercise a pinned old worker's real open/write guards after profile binding."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients/python"))
from star_vector_client import Store


def old_worker(source):
    env = dict(os.environ, STAR_VECTOR_V1_SOURCE=str(Path(source).resolve()))
    return subprocess.Popen([
        "sbcl", "--noinform", "--non-interactive", "--no-userinit", "--no-sysinit",
        "--eval", "(require :asdf)", "--eval",
        '(let ((*standard-output* *error-output*)) (asdf:load-asd (truename "lisp/star-vector-tek9.asd")) '
        '(asdf:load-system "star-vector-tek9") (load (uiop:getenv "STAR_VECTOR_V1_SOURCE")))',
        "--eval", "(star-vector-tek9:run)"], env=env, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def request(worker, value):
    worker.stdin.write(json.dumps(value)+"\n")
    worker.stdin.flush()
    line = worker.stdout.readline()
    if not line: raise RuntimeError("v1 worker exited without a response")
    return json.loads(line)


def main(source):
    with tempfile.TemporaryDirectory(prefix="star-vector-v1-profile-") as root:
        first, second = old_worker(source), None
        try:
            assert request(first, dict(op="open", path=root, dimensions=3))["ok"]
            with Store(root, 3) as current:
                current.bind_profile(dict(format="star-vector/embedding/1", dimensions=3, provider="test-fixture"))
                record = dict(document=dict(id="synthetic", dataset="synthetic", dtype="document", schemaVersion="0.10.1"), vector=[1, 0, 0])
                response = request(first, dict(op="upsert", id="synthetic", record=json.dumps(record)))
                assert response["ok"] is False, response
                assert len(current) == 0
                second = old_worker(source)
                response = request(second, dict(op="open", path=root, dimensions=3))
                assert response["ok"] is False and "supported Star Vector header" in response["error"], response
                assert len(current) == 0
        finally:
            for worker in [first, second]:
                if worker is not None:
                    worker.communicate(timeout=10)
                    assert worker.returncode == 0
    print("PASS: actual pinned v1 worker rejects stale writes and fresh opens of a profiled store")


if __name__ == "__main__": main(sys.argv[1])
