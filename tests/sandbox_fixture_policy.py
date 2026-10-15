"""Positive process fixtures request only limits enforced on the host platform."""

from __future__ import annotations

import os


def supported_process_limits() -> dict[str, int | None]:
    return {
        "file_size_bytes": None if os.name == "nt" else 33_554_432,
        "process_count": (
            None if os.name == "posix" and getattr(os, "getuid", lambda: -1)() == 0 else 32
        ),
    }
