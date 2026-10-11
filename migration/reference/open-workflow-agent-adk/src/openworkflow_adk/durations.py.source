"""Core duration parsing for the OpenWorkflow time forms.

Kept in core so task builders can depend on it without reaching into
:mod:`openworkflow_adk.ops` (which sits above core). The historical home in
:mod:`openworkflow_adk.ops.schedule` re-exports this function for callers.
"""

from __future__ import annotations

import re
from typing import Any


def duration_seconds(value: Any) -> float:
    """Convert the spec's common duration forms to seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return sum(
            float(value.get(unit, 0)) * factor
            for unit, factor in {
                "milliseconds": 0.001,
                "seconds": 1,
                "minutes": 60,
                "hours": 3600,
                "days": 86400,
            }.items()
        )
    match = re.fullmatch(
        r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?"
        r"(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        str(value),
    )
    if not match:
        raise ValueError(f"unsupported workflow duration: {value!r}")
    days, hours, minutes, seconds = match.groups()
    return (
        (float(days or 0) * 86400)
        + (float(hours or 0) * 3600)
        + (float(minutes or 0) * 60)
        + float(seconds or 0)
    )
