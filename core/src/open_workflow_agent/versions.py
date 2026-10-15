"""Semantic version 2.0 precedence, without package-manager range semantics."""

from __future__ import annotations

import re
from typing import Any

SEMANTIC_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)


def version_precedence(value: str) -> tuple[Any, ...] | None:
    match = SEMANTIC_VERSION.fullmatch(value) if len(value) <= 256 else None
    if match is None:
        return None
    pre = match.group(4)
    identifiers = (
        tuple((0, int(item)) if item.isdecimal() else (1, item) for item in pre.split("."))
        if pre is not None
        else ()
    )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), pre is None, identifiers)
