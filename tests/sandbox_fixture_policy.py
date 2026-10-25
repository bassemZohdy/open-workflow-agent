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


def javascript_process_limits() -> dict[str, int | None]:
    """Hosted UID quotas include unrelated runner threads; Docker proves NPROC.

    Node creates threads during startup. A quota of 32 cannot be applied to a
    shared runner UID already owning more than 32 threads. Physical image gates
    retain the production default under a dedicated non-root UID; isolated
    contract runners can select the same profile explicitly.
    """
    limits = supported_process_limits()
    if os.name == "posix" and os.getenv("OWA_TEST_ISOLATED_PROCESS_USER") != "1":
        limits["process_count"] = None
    return limits
