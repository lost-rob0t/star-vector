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


def check_store(source, cli, migrated):
    with tempfile.TemporaryDirectory(prefix="star-vector-v1-profile-") as root:
        path = str(Path(root) / "store")
        snapshot = Path(root) / "source.json"
        original = b'{"format":"star-vector/1","dimensions":3,"records":[]}'
        if migrated:
            snapshot.write_bytes(original)
            subprocess.run([cli, "migrate", str(snapshot), path, "3"], check=True,
                           capture_output=True, text=True, timeout=30)
        first, second, publisher = old_worker(source), None, None
        try:
            assert request(first, dict(op="open", path=path, dimensions=3))["ok"]
            # Open the current publisher before binding to exercise a stale
            # publication connection, not just a fresh format check.
            publisher = subprocess.Popen([os.environ["STAR_VECTOR_TEK9_WORKER"]], text=True,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL)
            assert request(publisher, dict(op="open", path=path, dimensions=3))["ok"]
            with Store(path, 3) as current:
                profile = dict(format="star-vector/embedding/1", dimensions=3, provider="test-fixture")
                current.bind_profile(profile)
                response = request(first, dict(op="publish"))
                assert response["ok"] is False, ("v1 publish bypassed binding", response)
                assert request(publisher, dict(op="publish"))["ok"]
                if migrated:
                    replay = subprocess.run([cli, "migrate", str(snapshot), path, "3"], check=True,
                                            capture_output=True, text=True, timeout=30)
                    assert json.loads(replay.stdout)["complete"] is True
                    assert snapshot.read_bytes() == original
                assert current.profile == profile
                record = dict(document=dict(id="synthetic", dataset="synthetic", dtype="document", schemaVersion="0.10.1"), vector=[1, 0, 0])
                response = request(first, dict(op="upsert", id="synthetic", record=json.dumps(record)))
                assert response["ok"] is False, response
                assert len(current) == 0
                second = old_worker(source)
                response = request(second, dict(op="open", path=path, dimensions=3))
                assert response["ok"] is False and "supported Star Vector header" in response["error"], response
                assert len(current) == 0
            with Store(path, 3) as reopened:
                assert reopened.profile == profile and len(reopened) == 0
        finally:
            for worker in [first, second, publisher]:
                if worker is not None:
                    worker.communicate(timeout=10)
                    assert worker.returncode == 0


def main(source, cli):
    for migrated in [False, True]:
        check_store(source, cli, migrated)
    print("PASS: profile isolation survives migration replay, stale publication and actual v1 writes/opens")


if __name__ == "__main__": main(sys.argv[1], sys.argv[2])
