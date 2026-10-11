"""Shared translation helpers used by task builders."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from collections.abc import Callable
from typing import Any

from openworkflow_adk.models import Task

NodeBuilder = Callable[[str, Task], Any]


def _sandbox_preexec(limits: dict[str, Any] | None) -> None:
    """Apply best-effort POSIX limits before executing user code.

    This is not a network sandbox: without a separate network namespace or
    seccomp profile, sandboxed code may still open outbound sockets.
    """
    if os.name != "posix":
        return
    import resource

    try:
        os.setsid()
    except PermissionError:
        # Some hardened/containerized hosts disallow creating a new session;
        # resource limits still apply and timeout cleanup falls back to the
        # child process itself on such hosts.
        pass
    limits = limits or {}
    values = (
        ("cpu_seconds", resource.RLIMIT_CPU),
        ("memory_bytes", resource.RLIMIT_AS),
        ("nofile", resource.RLIMIT_NOFILE),
    )
    for key, resource_kind in values:
        value = limits.get(key)
        if value is not None:
            amount = int(value)
            resource.setrlimit(resource_kind, (amount, amount))
    if sys.platform.startswith("linux"):
        import ctypes

        ctypes.CDLL(None).prctl(38, 1, 0, 0, 0)


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (PermissionError, ProcessLookupError):
            proc.kill()
            return
    proc.kill()


def _adk_name(name: str) -> str:
    value = re.sub(r"\W", "_", name)
    return value if value and not value[0].isdigit() else f"workflow_{value}"


async def _noop(ctx: Any) -> None:
    return None
