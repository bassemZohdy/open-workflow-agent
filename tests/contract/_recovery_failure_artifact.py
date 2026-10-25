"""Secret-safe diagnostics for subprocess contract-test failures."""

from __future__ import annotations

import re
import traceback
from pathlib import Path
from typing import Any

from open_workflow_agent.errors import OwaError

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,120}\Z")
_SAFE_PROFILE_PATH = re.compile(r"[A-Za-z0-9_.\[\]-]{1,240}\Z")


def failure_artifact(
    error: BaseException, *, stage: str, include_traceback: bool = False
) -> dict[str, Any]:
    """Capture useful exception classification without serializing arbitrary values."""
    artifact: dict[str, Any] = {
        "stage": stage,
        "exception": type(error).__name__,
        "type": type(error).__name__,
        "code": getattr(error, "code", None),
    }
    if isinstance(error, OwaError):
        details: dict[str, Any] = {}
        reason = error.details.get("reason")
        if isinstance(reason, str) and _SAFE_TOKEN.fullmatch(reason):
            details["reason"] = reason
        changed_fields = error.details.get("changed_fields")
        if isinstance(changed_fields, (list, tuple)) and all(
            isinstance(path, str) and _SAFE_PROFILE_PATH.fullmatch(path) for path in changed_fields
        ):
            details["changed_fields"] = list(changed_fields)
        artifact["details"] = details
        artifact["error"] = {"code": artifact["code"], "details": details}
    sqlite_errorcode = getattr(error, "sqlite_errorcode", None)
    if isinstance(sqlite_errorcode, int):
        artifact["sqlite_errorcode"] = sqlite_errorcode
    sqlite_errorname = getattr(error, "sqlite_errorname", None)
    if isinstance(sqlite_errorname, str) and _SAFE_TOKEN.fullmatch(sqlite_errorname):
        artifact["sqlite_errorname"] = sqlite_errorname
    if include_traceback:
        artifact["frames"] = [
            {
                "file": Path(frame.filename).name,
                "line": frame.lineno,
                "function": frame.name,
            }
            for frame in traceback.extract_tb(error.__traceback__)
        ]
    return artifact
