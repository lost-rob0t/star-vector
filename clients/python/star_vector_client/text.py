"""Pinned, local English static embeddings. No network access or implicit downloads."""
import copy
import hashlib
import importlib.resources
import json
import math
import os
from pathlib import Path

from .native import Store


class SpacyEncoder:
    """Reuse one encoder across documents/stores. Model assets are verified on load."""
    dimensions = 300

    def __init__(self, model_path=None):
        import numpy as np
        import spacy
        manifest = json.loads(importlib.resources.files(__package__).joinpath("model.json").read_text())
        if spacy.__version__ != manifest["providerVersion"]:
            raise ValueError("spaCy version mismatch: expected " + manifest["providerVersion"])
        model_path = model_path or os.environ.get("STAR_VECTOR_MODEL")
        if model_path is None:
            import en_core_web_md
            model_path = Path(en_core_web_md.__file__).parent / "en_core_web_md-3.8.0"
        root = Path(model_path)
        for name, expected in manifest["files"].items():
            with (root / name).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("embedding model artifact mismatch: " + name)
        self._nlp = spacy.load(root, exclude=[
            "tok2vec", "tagger", "parser", "senter", "attribute_ruler", "lemmatizer", "ner"])
        if self._nlp.vocab.vectors_length != self.dimensions:
            raise ValueError("embedding model dimension mismatch")
        self._profile = {
            "format": "star-vector/embedding/1", "dimensions": self.dimensions,
            "provider": "spacy.Doc.vector", "providerVersion": spacy.__version__,
            "numpyVersion": np.__version__, "model": manifest["model"],
            "modelVersion": manifest["modelVersion"], "modelSha256": manifest["wheelSha256"],
            "artifactSha256": manifest["files"], "language": "en",
            "extraction": "rawContent-verbatim-v1", "preprocessing": "spacy-tokenizer-all-tokens-v1",
            "pooling": "spacy.Doc.vector-mean-v1", "normalization": "l2-float64-fsum-then-float32-v1",
            "maxCharacters": 1000000, "overflow": "reject", "zeroVector": "reject",
        }

    @property
    def profile(self):
        return copy.deepcopy(self._profile)

    def encode(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if len(text) > self._profile["maxCharacters"]:
            raise ValueError("text exceeds 1000000 characters; no truncation was performed")
        # The producer performs tokenization and mean pooling; no pipeline inference.
        vector = self._nlp.make_doc(text).vector.astype("float64")
        norm = math.sqrt(math.fsum(float(x) * float(x) for x in vector))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("text has no non-zero finite embedding in the pinned vocabulary")
        return (vector / norm).astype("float32").tolist()


class DocumentStore:
    """Canonical rawContent -> encoder -> C ABI -> Tek9. An encoder supplies profile and encode(text)."""
    def __init__(self, path, encoder=None, library=None):
        self.encoder = encoder if encoder is not None else SpacyEncoder()
        self.profile = copy.deepcopy(self.encoder.profile)
        self.store = Store(path, self.profile["dimensions"], library)
        try:
            self.store.bind_profile(self.profile)
        except BaseException:
            self.store.close()
            raise

    def _compatible(self):
        if self.encoder.profile != self.profile:
            raise ValueError("encoder profile changed after opening the store")

    def upsert(self, document):
        self._compatible()
        if not isinstance(document, dict):
            raise ValueError("document must be a canonical StarIntel JSON object")
        text = document.get("rawContent")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("canonical document rawContent must be a non-empty string")
        vector = self.encoder.encode(text)
        result = copy.deepcopy(document)
        extensions = result.setdefault("extensions", {})
        if not isinstance(extensions, dict):
            raise ValueError("document extensions must be an object")
        key = "star-vector.embedding"
        if key in extensions and not (
            isinstance(extensions[key], dict) and set(extensions[key]) == {"profile", "textSha256"}
            and isinstance(extensions[key]["profile"], dict)
            and extensions[key]["profile"].get("format") == "star-vector/embedding/1"
        ):
            raise ValueError("reserved extension star-vector.embedding already contains unrelated data")
        # This one documented namespace is maintained on every text update.
        extensions[key] = {
            "profile": copy.deepcopy(self.profile),
            "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        self.store.upsert(result, vector, self.profile)
        return result

    def search(self, text, metric="cosine", limit=10):
        self._compatible()
        return self.store.search(self.encoder.encode(text), metric, limit)

    def get(self, identifier):
        return self.store.get(identifier)

    def delete(self, identifier):
        return self.store.delete(identifier)

    def close(self):
        self.store.close()

    def __enter__(self):
        self.store._open()
        return self

    def __exit__(self, *_):
        self.close()
