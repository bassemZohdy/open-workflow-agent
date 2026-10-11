"""Tiny, dependency-free helpers shared across the package.

These helpers are intentionally boring: they avoid pulling in non-stdlib
dependencies so every layer can use them without creating import cycles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def response_body(response: Any) -> Any:
    """Extract a JSON or text body from an httpx-like response object."""
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    return response.text
