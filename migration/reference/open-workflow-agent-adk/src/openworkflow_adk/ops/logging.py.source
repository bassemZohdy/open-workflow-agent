"""Structured run logging hooks."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from openworkflow_adk._utils import utc_now_iso

__all__ = ["JsonRunLogger"]


class JsonRunLogger:
    """Emit one JSON object per run/task lifecycle event."""

    def __init__(self, stream: TextIO = sys.stderr) -> None:
        self.stream = stream

    def __call__(self, record: dict[str, Any]) -> None:
        self.stream.write(json.dumps({"timestamp": utc_now_iso(), **record}, default=str) + "\n")
        self.stream.flush()
