"""Remember a finished file parse so a stopped index can continue.

The stored value is the same product the walker just built. A later generation
loads it only when the path, bytes, parser, and embedding fingerprint match.
"""

from __future__ import annotations

import hashlib
import os
import pickle
from pathlib import Path

from app.ingestion.versions import CHUNKING_VERSION, PARSER_VERSION
from app.storage.identity import content_hash


class ParseCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, path: str, payload: bytes, fingerprint: str):
        cached = self._path(path, payload, fingerprint)
        try:
            data = cached.read_bytes()
        except OSError:
            return None
        try:
            return pickle.loads(data)
        except (pickle.UnpicklingError, EOFError, AttributeError, ValueError, TypeError):
            return None

    def store(self, path: str, payload: bytes, fingerprint: str, product) -> None:
        cached = self._path(path, payload, fingerprint)
        temporary = cached.with_suffix(".tmp")
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(pickle.dumps(product, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(temporary, cached)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _path(self, path: str, payload: bytes, fingerprint: str) -> Path:
        material = "\n".join((PARSER_VERSION, CHUNKING_VERSION, fingerprint, path, content_hash(payload)))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest
