"""Owned ctypes bindings. Serialize calls on each Store; independent stores may run concurrently."""
import ctypes as C
import json
import os
import sys


class StarVectorError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class Store:
    METRICS = {"cosine": 0, "dot": 1, "euclidean": 2, "manhattan": 3}

    def __init__(self, path, dimensions, library=None):
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 0 < dimensions <= sys.maxsize:
            raise ValueError("dimensions must be a positive integer")
        self.lib = C.CDLL(str(library or os.environ.get("STAR_VECTOR_LIBRARY", "libstar_vector.so")))
        p, s, n, f = C.c_void_p, C.c_char_p, C.c_size_t, C.POINTER(C.c_float)
        signatures = {
            "sv_open": ([s, n, C.POINTER(p)], C.c_int32),
            "sv_close": ([p], C.c_int32), "sv_flush": ([p], C.c_int32),
            "sv_len": ([p], n), "sv_last_error": ([p], s),
            "sv_upsert_document": ([p, s, f, n], C.c_int32),
            "sv_upsert_document_with_profile": ([p, s, f, n, s], C.c_int32),
            "sv_bind_embedding_profile": ([p, s], C.c_int32),
            "sv_get_embedding_profile": ([p, C.POINTER(p)], C.c_int32),
            "sv_get_document": ([p, s, C.POINTER(p)], C.c_int32),
            "sv_delete_document": ([p, s, C.POINTER(C.c_int32)], C.c_int32),
            "sv_search_json": ([p, f, n, C.c_int, n, C.POINTER(p)], C.c_int32),
            "sv_string_free": ([p], None),
        }
        for name, (args, restype) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, restype
        self.handle = p()
        self.dimensions = dimensions
        status = self.lib.sv_open(self._string(os.fspath(path)), dimensions, C.byref(self.handle))
        self._check(status)

    @staticmethod
    def _string(value):
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("expected a string without NUL")
        return value.encode("utf-8")

    def _open(self):
        if not self.handle:
            raise ValueError("store is closed")

    def _check(self, status):
        if status:
            message = self.lib.sv_last_error(self.handle)
            raise StarVectorError(status, (message or b"unknown Star Vector error").decode("utf-8"))

    def _json(self, fn, *args):
        self._open()
        out = C.c_void_p()
        self._check(fn(self.handle, *args, C.byref(out)))
        try:
            return json.loads(C.string_at(out).decode("utf-8"))
        finally:
            self.lib.sv_string_free(out)

    def close(self):
        if self.handle:
            self._check(self.lib.sv_close(self.handle))
            self.handle = C.c_void_p()

    def __enter__(self):
        self._open()
        return self

    def __exit__(self, *_):
        self.close()

    def __len__(self):
        self._open()
        count = self.lib.sv_len(self.handle)
        error = self.lib.sv_last_error(self.handle)
        if error:
            raise StarVectorError(4, error.decode("utf-8"))
        return count

    @property
    def profile(self):
        return self._json(self.lib.sv_get_embedding_profile)

    def bind_profile(self, profile):
        self._open()
        self._check(self.lib.sv_bind_embedding_profile(self.handle, encoded(profile)))

    def upsert(self, document, vector, profile=None):
        self._open()
        values = (C.c_float * len(vector))(*vector)
        if profile is None:
            status = self.lib.sv_upsert_document(self.handle, encoded(document), values, len(values))
        else:
            status = self.lib.sv_upsert_document_with_profile(
                self.handle, encoded(document), values, len(values), encoded(profile))
        self._check(status)

    def get(self, identifier):
        return self._json(self.lib.sv_get_document, self._string(identifier))

    def delete(self, identifier):
        self._open()
        deleted = C.c_int32()
        self._check(self.lib.sv_delete_document(self.handle, self._string(identifier), C.byref(deleted)))
        return bool(deleted.value)

    def search(self, vector, metric="cosine", limit=10):
        self._open()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= sys.maxsize:
            raise ValueError("limit must be a non-negative integer")
        if metric not in self.METRICS:
            raise ValueError("unsupported vector metric")
        values = (C.c_float * len(vector))(*vector)
        return self._json(self.lib.sv_search_json, values, len(values), self.METRICS[metric], limit)
