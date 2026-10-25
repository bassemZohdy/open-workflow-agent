"""Decode the URI layer of bounded local JSON Pointer references."""

from __future__ import annotations

import re
from urllib.parse import unquote


def decode_local_pointer(reference: str) -> str:
    """Decode once before pointer tokenization; callers own error translation."""
    if not reference.startswith("#/") or re.search(r"%(?![0-9A-Fa-f]{2})", reference):
        raise ValueError("invalid local reference")
    return unquote(reference[1:], encoding="utf-8", errors="strict")
