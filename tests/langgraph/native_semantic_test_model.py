"""Deterministic offline fixture, also importable by the abrupt-exit worker."""

import hashlib


class LocalTestModel:
    artifact_sha256 = hashlib.sha256(b"deterministic semantic fixture model v1").hexdigest()

    def __init__(self):
        self.calls = []
        self.failures = []

    def embed(self, text):
        self.calls.append(text)
        vector = [0.0] * 384
        vector[0 if any(word in text.lower() for word in ("coffee", "espresso")) else 1] = 1.0
        return vector
