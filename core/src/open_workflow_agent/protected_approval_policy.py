"""Finite approval business policy carried by standard workflow event data.

This is a common protected service profile, not a new workflow task or a native
metadata extension. Schema documents use the ordinary common OWS data validator.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import ApprovalValidationError, OwaError
from .expressions import _segments, validate_expression_data

PROTECTED_APPROVAL_PROFILE = "owa/protected-approval/v1"
MAX_APPROVAL_POLICY_BYTES = 65_536
MAX_APPROVAL_VALUE_BYTES = 262_144
_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}")


def bounded_json(value: Any, limit: int = MAX_APPROVAL_VALUE_BYTES) -> str:
    try:
        validate_expression_data(value)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8", errors="strict")) > limit:
            raise ValueError
        return encoded
    except (OwaError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ApprovalValidationError("approval value exceeds its finite JSON profile") from None


def actor_identity(value: Any, *, internal: bool = False) -> str:
    if (
        not isinstance(value, str)
        or _ACTOR.fullmatch(value) is None
        or (value.startswith("system:") and not internal)
    ):
        raise ApprovalValidationError("approval actor identity is invalid")
    return value


def original_expiry(value: Any) -> float:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", value
        )
        is None
    ):
        raise ApprovalValidationError("protected approval expiry must be absolute RFC 3339 UTC")
    try:
        selected = datetime.fromisoformat(value[:-1] + "+00:00")
        offset = selected.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError
        result = selected.timestamp()
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, OverflowError, OSError):
        raise ApprovalValidationError(
            "protected approval expiry must be absolute RFC 3339 UTC"
        ) from None


@dataclass(frozen=True, slots=True)
class ProtectedApprovalPolicy:
    assignee: str
    reassignment_targets: tuple[str, ...]
    escalation_targets: tuple[str, ...]
    schema_json: str | None = None

    @classmethod
    def declared_actors(cls, value: Any) -> frozenset[str]:
        """Validate the compiled envelope and return only known literal actors.

        OWS compilation owns expression syntax and variable scopes. Dynamic
        values are validated again in full by the atomic request participant;
        a source expression cannot grant an actor or create a request row.
        """
        bounded_json(value, MAX_APPROVAL_POLICY_BYTES)
        prepared = copy.deepcopy(value)
        if not isinstance(prepared, dict):
            cls.from_value(prepared)
            raise ApprovalValidationError("protected approval policy is invalid")

        def expression(item: Any, *, whole: bool = False) -> bool:
            if not isinstance(item, str) or "${" not in item:
                return False
            try:
                segments = _segments(item)
            except OwaError:
                raise ApprovalValidationError("approval policy expression is invalid") from None
            executable = [source for enabled, source in segments if enabled]
            if not executable or any(not source.strip() for source in executable):
                raise ApprovalValidationError("approval policy expression is invalid")
            if whole and (
                len(executable) != 1
                or any(source.strip() for enabled, source in segments if not enabled)
            ):
                raise ApprovalValidationError("approval assignment targets are invalid")
            return True

        dynamic_assignee = expression(prepared.get("assignee"))
        if dynamic_assignee:
            # This private validation copy is never executed or retained.
            prepared["assignee"] = "static-admission-placeholder"
        for name in ("reassignmentTargets", "escalationTargets"):
            items = prepared.get(name)
            if expression(items, whole=True):
                prepared[name] = []
            elif isinstance(items, list):
                if len(items) > 32:
                    raise ApprovalValidationError("approval assignment targets are invalid")
                prepared[name] = [item for item in items if not expression(item)]
        policy = cls.from_value(prepared)
        return frozenset(
            {
                *(() if dynamic_assignee else (policy.assignee,)),
                *policy.reassignment_targets,
                *policy.escalation_targets,
            }
        )

    @classmethod
    def from_value(cls, value: Any) -> ProtectedApprovalPolicy:
        if (
            not isinstance(value, dict)
            or set(value)
            - {"profile", "assignee", "reassignmentTargets", "escalationTargets", "responseSchema"}
            or not {"profile", "assignee", "reassignmentTargets", "escalationTargets"}.issubset(
                value
            )
            or value.get("profile") != PROTECTED_APPROVAL_PROFILE
        ):
            raise ApprovalValidationError("protected approval policy is invalid")
        bounded_json(value, MAX_APPROVAL_POLICY_BYTES)
        targets = []
        for name in ("reassignmentTargets", "escalationTargets"):
            items = value[name]
            if not isinstance(items, list) or len(items) > 32:
                raise ApprovalValidationError("approval assignment targets are invalid")
            selected = tuple(actor_identity(item) for item in items)
            if len(set(selected)) != len(selected):
                raise ApprovalValidationError("approval assignment targets must be unique")
            targets.append(selected)
        schema = value.get("responseSchema")
        if "responseSchema" in value and schema is None:
            raise ApprovalValidationError(
                "approval response schema must be an inline OWS data schema"
            )
        if schema is not None:
            if (
                not isinstance(schema, Mapping)
                or set(schema) - {"format", "document"}
                or "document" not in schema
            ):
                raise ApprovalValidationError(
                    "approval response requires an inline OWS data schema"
                )
            from .workflow import _validate_data_profile

            try:
                _validate_data_profile(
                    {"input": {"schema": schema}}, "/approvalPolicy/responseSchema"
                )
            except OwaError:
                raise ApprovalValidationError(
                    "approval response schema is invalid or unsupported"
                ) from None
        return cls(
            actor_identity(value["assignee"]),
            targets[0],
            targets[1],
            bounded_json(schema) if schema is not None else None,
        )

    def as_value(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "profile": PROTECTED_APPROVAL_PROFILE,
            "assignee": self.assignee,
            "reassignmentTargets": list(self.reassignment_targets),
            "escalationTargets": list(self.escalation_targets),
        }
        if self.schema_json is not None:
            value["responseSchema"] = json.loads(self.schema_json)
        return value

    def validate_response(self, value: Any) -> None:
        bounded_json(value)
        if self.schema_json is None:
            return
        from .catalog import FunctionCatalog
        from .workflow import WorkflowExecutor

        try:
            # Reuse the common offline OWS validation path and its local-only
            # registry. No alternate schema dialect, retrieval or dispatch.
            WorkflowExecutor(FunctionCatalog())._validate_data_schema(
                {"schema": copy.deepcopy(json.loads(self.schema_json))}, value, "approval response"
            )
        except OwaError:
            raise ApprovalValidationError(
                "approval response does not satisfy its original schema"
            ) from None
