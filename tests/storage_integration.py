#!/usr/bin/env python3
"""Synthetic integration tests; every mutable path lives in TemporaryDirectory."""
import concurrent.futures
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def document(identifier):
    return dict(id=identifier, dataset="synthetic", dtype="document", schemaVersion="0.10.1")


class Client:
    def __init__(self, library):
        self.lib = C.CDLL(str(library))
        declarations = {
            "sv_open": ([C.c_char_p, C.c_size_t, C.POINTER(C.c_void_p)], C.c_int),
            "sv_close": ([C.c_void_p], C.c_int),
            "sv_len": ([C.c_void_p], C.c_size_t),
            "sv_upsert_document": ([C.c_void_p, C.c_char_p, C.POINTER(C.c_float), C.c_size_t], C.c_int),
            "sv_get_document": ([C.c_void_p, C.c_char_p, C.POINTER(C.c_void_p)], C.c_int),
            "sv_last_error": ([C.c_void_p], C.c_char_p),
            "sv_string_free": ([C.c_void_p], None),
            "sv_search_json": ([C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.c_int, C.c_size_t, C.POINTER(C.c_void_p)], C.c_int),
        }
        for name, (args, result) in declarations.items():
            getattr(self.lib, name).argtypes = args
            getattr(self.lib, name).restype = result

    def check(self, status, handle=None):
        assert status == 0, (status, self.lib.sv_last_error(handle))

    def open(self, path, dimensions=3):
        handle = C.c_void_p()
        self.check(self.lib.sv_open(os.fsencode(path), dimensions, C.byref(handle)))
        return handle

    def put(self, handle, identifier, vector=(1, 0, 0)):
        values = (C.c_float * len(vector))(*vector)
        return self.lib.sv_upsert_document(handle, json.dumps(document(identifier)).encode(), values, len(vector))

    def get(self, handle, identifier):
        result = C.c_void_p()
        status = self.lib.sv_get_document(handle, identifier.encode(), C.byref(result))
        try:
            value = json.loads(C.string_at(result)) if result else None
            return status, value
        finally:
            self.lib.sv_string_free(result)

    def search(self, handle, metric, limit=10, query=(1, 0, 0)):
        result = C.c_void_p()
        self.check(self.lib.sv_search_json(handle, (C.c_float * len(query))(*query), len(query), metric, limit, C.byref(result)), handle)
        try:
            return json.loads(C.string_at(result))
        finally:
            self.lib.sv_string_free(result)


def children(pid=None):
    return set(map(int, Path(f"/proc/{pid or os.getpid()}/task/{pid or os.getpid()}/children").read_text().split()))


def exited(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


def wait_exited(pid):
    deadline = time.monotonic() + 10
    while not exited(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert exited(pid), f"orphan worker {pid} survived parent exit"


def main(cli, library):
    client = Client(library)
    baseline_children = children()
    def command(*args, success=True):
        p = subprocess.run([cli, *map(str, args)], capture_output=True, text=True, timeout=30)
        assert (p.returncode == 0) == success, (args, p.returncode, p.stdout, p.stderr)
        return json.loads(p.stdout) if success else p

    with tempfile.TemporaryDirectory(prefix="sv-integration-") as root:
        root = Path(root)
        path = root / "live"
        a = client.open(path)
        b = client.open(path)
        assert len(children() - baseline_children) == 2
        client.check(client.put(a, "doc:a"), a)
        assert client.lib.sv_len(b) == 1  # Fresh transaction sees the other handle's commit.
        client.check(client.put(b, "doc:b"), b)
        assert client.lib.sv_len(a) == 2
        assert [r["id"] for r in client.search(a, 1, 1)] == ["doc:a"]
        assert client.get(b, "doc:a")[1] == document("doc:a")
        # Invalid input never reaches storage.
        assert client.put(a, "x" * 600) == 2
        assert client.lib.sv_len(b) == 2
        client.check(client.put(a, "x" * 512), a)
        assert client.get(b, "x" * 512)[1] == document("x" * 512)
        client.check(client.lib.sv_close(a))
        client.check(client.lib.sv_close(b))
        assert children() == baseline_children
        assert command("get", path, 3, "doc:b") == document("doc:b")

        # Concurrent CLI handles/processes must not lose unrelated writes.
        def writer(i):
            file = root / f"doc-{i}.json"
            file.write_text(json.dumps(document(f"concurrent:{i}")))
            command("upsert", path, 3, file, "0,1,0")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(writer, range(12)))
        a = client.open(path)
        assert client.lib.sv_len(a) == 15
        # Every metric's bounded prefix matches its full exact ordering, including ties.
        for metric in range(4):
            all_rows = client.search(a, metric, 100)
            for k in (0, 1, 3, 20):
                assert client.search(a, metric, k) == all_rows[:k]
        worker, = children() - baseline_children
        os.kill(worker, signal.SIGKILL)
        status, value = client.get(a, "doc:a")
        assert status == 4 and value is None
        assert client.lib.sv_last_error(a)
        client.check(client.lib.sv_close(a))
        assert children() == baseline_children
        assert command("get", path, 3, "doc:a") == document("doc:a")

        # C ABI writes are read by Lisp, Lisp commits read by CLI after close/reopen.
        env = os.environ | {"STAR_VECTOR_LIBRARY": str(library), "SV_TEST_STORE": str(path),
                                   "C_INCLUDE_PATH": str(Path(__file__).resolve().parents[1] / "include")}
        subprocess.run(["sbcl", "--noinform", "--non-interactive", "--no-userinit", "--no-sysinit",
                        "--load", str(Path(__file__).with_name("bridge_lisp.lisp"))],
                       env=env, check=True, timeout=60)
        assert command("get", path, 3, "doc:lisp-bridge") == document("doc:lisp-bridge")

        # Kill only the C client: its workers must observe EOF and close themselves,
        # even with two handles (no inherited pipe endpoints keeping either alive).
        child = subprocess.Popen([sys.executable, __file__, "--crash", str(library), str(path)],
                                 stdout=subprocess.PIPE, text=True)
        try:
            worker_pids = json.loads(child.stdout.readline())
            assert len(worker_pids) == 2
            child.kill()
            child.wait(timeout=10)
            for pid in worker_pids: wait_exited(pid)
        finally:
            if child.poll() is None: child.kill(); child.wait()
        assert command("get", path, 3, "doc:ack-before-crash") == document("doc:ack-before-crash")

        source = root / "legacy.json"
        snapshot = dict(format="star-vector/1", dimensions=3, records=[
            dict(document=document(f"old:{i:03}"), vector=[1.0, float(i % 3), 0.0]) for i in range(270)])
        original = json.dumps(snapshot).encode()
        source.write_bytes(original)
        destination = root / "migrated"
        assert command("get", source, 3, "old:000") == document("old:000")
        command("delete", source, 3, "old:000", success=False)
        assert source.read_bytes() == original
        partial = command("migrate", source, destination, 3, 129)
        assert not partial["complete"] and partial["checkpoint"] == 129
        command("get", destination, 3, "old:000", success=False)
        source.write_bytes(original + b"\n")
        command("migrate", source, destination, 3, success=False)
        source.write_bytes(original)
        complete = command("migrate", source, destination, 3)
        assert complete["complete"] and complete["checkpoint"] == 270
        assert command("migrate", source, destination, 3)["complete"]  # Idempotent.
        assert source.read_bytes() == original
        assert command("search", source, 3, "dot", 7, "1,0,0") == command("search", destination, 3, "dot", 7, "1,0,0")
        command("migrate", source, path, 3, success=False)
        command("migrate", source, source, 3, success=False)
        # Validate every source row before creating ANY destination.
        snapshot["records"][-1]["document"]["schemaVersion"] = "0.9.0"
        source.write_text(json.dumps(snapshot))
        bad_dest = root / "bad"
        command("migrate", source, bad_dest, 3, success=False)
        assert not bad_dest.exists()
        source.write_bytes(original)
        # Kill a migrator after an acknowledged atomic batch; restart converges.
        from_worker = root / "crash-migration"
        worker = subprocess.Popen([os.environ["STAR_VECTOR_TEK9_WORKER"]], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        def request(value):
            worker.stdin.write(json.dumps(value) + "\n"); worker.stdin.flush()
            return json.loads(worker.stdout.readline())
        try:
            assert request(dict(op="open", path=str(from_worker), dimensions=3,
                                source=hashlib.sha1(original).hexdigest().upper(), total=270))["ok"]
            batch = [dict(id=r["document"]["id"], record=json.dumps(r)) for r in snapshot["records"][:5]]
            assert request(dict(op="migrate-batch", expected=0, records=batch))["ok"]
            # A duplicate later in a batch must abort its earlier successful put.
            duplicate = [dict(id="old:005", record=json.dumps(snapshot["records"][5])), batch[0]]
            assert not request(dict(op="migrate-batch", expected=5, records=duplicate))["ok"]
            state = request(dict(op="status"))
            assert state["next"] == state["count"] == 5
            worker.kill(); worker.wait(timeout=10)
        finally:
            if worker.poll() is None: worker.kill(); worker.wait()
        # Kill while the producer transaction contains uncommitted data AND metadata.
        transaction = subprocess.Popen([
            "sbcl", "--noinform", "--non-interactive", "--no-userinit", "--no-sysinit",
            "--load", str(Path(__file__).with_name("tek9_crash_transaction.lisp"))],
            env=os.environ | {"SV_TEST_STORE": str(from_worker)}, stdout=subprocess.PIPE, text=True)
        try:
            # CFFI's external compiler can write to the inherited OS stdout even
            # while Lisp *standard-output* is bound to *error-output*.
            for line in transaction.stdout:
                if line.strip() == "transaction-open": break
            else:
                raise AssertionError("producer exited before opening the crash-test transaction")
            transaction.kill(); transaction.wait(timeout=10)
        finally:
            if transaction.poll() is None: transaction.kill(); transaction.wait()
        assert command("migrate", source, from_worker, 3)["complete"]
        assert command("get", from_worker, 3, "old:269") == document("old:269")

        env = os.environ | {"STAR_VECTOR_TEK9_WORKER": "/bin/false"}
        failed = subprocess.run([cli, "init", str(root / "startup-failure"), "3"], env=env,
                                capture_output=True, text=True, timeout=10)
        assert failed.returncode != 0 and "worker" in failed.stderr.lower()
    print("PASS: CLI/C/Lisp persistence, concurrent handles, worker failure/EOF cleanup, atomic migration/restart/readback")


if __name__ == "__main__":
    if sys.argv[1] == "--crash":
        c = Client(sys.argv[2]); a = c.open(sys.argv[3]); b = c.open(sys.argv[3])
        c.check(c.put(a, "doc:ack-before-crash"), a)
        print(json.dumps(sorted(children())), flush=True)
        signal.pause()
    else:
        main(str(Path(sys.argv[1]).resolve()), str(Path(sys.argv[2]).resolve()))
