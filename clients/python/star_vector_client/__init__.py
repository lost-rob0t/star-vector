"""Local Star Vector C ABI and document embedding clients."""
from .native import Store, StarVectorError
from .text import DocumentStore, SpacyEncoder

__all__ = ["Store", "StarVectorError", "DocumentStore", "SpacyEncoder"]
