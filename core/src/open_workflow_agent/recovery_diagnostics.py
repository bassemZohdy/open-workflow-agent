"""Bounded read-only common recovery inspection, without native or external I/O."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .effects import EffectIdentity
from .lifecycle import TERMINAL_INVOCATION_STATES
from .tracing import invocation_trace_id

if TYPE_CHECKING:
    from .persistence import ExecutionHandle
    from .services import RuntimeServices

_MAX_EFFECTS = 100
_MAX_IDENTITY_BYTES = 8192
_MAX_RECEIPT_BYTES = 65536
_DEFINITION_REASONS = frozenset(
    {
        "archive_missing",
        "archive_invalid",
        "binding_missing",
        "binding_invalid",
        "runtime_profile_changed",
        "definition_changed",
        "input_missing",
        "input_changed",
        "runtime_profile_unavailable",
        "archive_limit_exceeded",
        "deployment_profile_unqualified",
        "deployment_trust_changed",
    }
)


def recovery_diagnostics(services: RuntimeServices, handle: ExecutionHandle) -> dict[str, Any]:
    """Report only verified common evidence, never claim native resume readiness.

    The caller must enforce object authorization before calling this function.
    Inspection never acquires ownership, resumes an invocation, queries a
    receiver, or initializes/migrates a native database. Receipt sampling is
    capped before decoding; an exceeded bound is an explicit incomplete result.
    """
    report: dict[str, Any] = {
        "profile": "owa/recovery-diagnostics/v1",
        "invocation_id": handle.invocation_id,
        "status": handle.status,
        "definition": {
            "name": handle.workflow_name,
            "version": handle.workflow_version,
            "fingerprint": handle.workflow_fingerprint,
        },
        "engine": handle.engine,
        "continuation": "not_inspected",
        "native": "not_inspected",
        "checks": [],
        "actions": {"known": 0, "pending": 0, "complete": False},
        "ownership": "not_inspected",
    }
    checks: list[dict[str, str]] = report["checks"]
    if services.config.observability.tracing.enabled:
        report["trace"] = {
            "trace_id": invocation_trace_id(handle.invocation_id),
            "parent_invocation_trace_id": (
                invocation_trace_id(handle.parent_invocation_id)
                if handle.parent_invocation_id is not None
                else None
            ),
            "execution_authority": False,
        }
    if handle.status in TERMINAL_INVOCATION_STATES:
        report["continuation"] = "terminal_result"
        checks.append(
            {
                "stage": "invocation",
                "outcome": "terminal",
                "reason": "immutable_terminal_result",
                "condition": "read the original common result; no continuation is required",
            }
        )
        return report

    try:
        snapshot = services.recovery_definitions.diagnostic_safe_snapshot(handle)
        compatible = snapshot.get("status") == "compatible"
        reason = snapshot.get("reason")
        if not compatible:
            reason = reason if reason in _DEFINITION_REASONS else "definition_evidence_unavailable"
            checks.append(
                {
                    "stage": "definition",
                    "outcome": "unavailable",
                    "reason": reason,
                    "condition": "restore the originally admitted compatible definition evidence",
                }
            )
            report["continuation"] = "unavailable"
            return report
        provenance = snapshot.get("provenance")
        if (
            isinstance(provenance, dict)
            and set(provenance) == {"definition_digest", "runtime_profile_digest"}
            and all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in provenance.values()
            )
        ):
            report["provenance"] = dict(provenance)
    except Exception:
        checks.append(
            {
                "stage": "definition",
                "outcome": "unavailable",
                "reason": "definition_evidence_unavailable",
                "condition": "restore the originally admitted compatible definition evidence",
            }
        )
        report["continuation"] = "unavailable"
        return report
    checks.append(
        {
            "stage": "definition",
            "outcome": "verified",
            "reason": "original_definition_retained",
            "condition": "continue with the originally admitted definition",
        }
    )
    try:
        services.invocations.read_input(handle)
    except Exception:
        report["continuation"] = "unavailable"
        checks.append(
            {
                "stage": "input",
                "outcome": "unavailable",
                "reason": "original_input_unavailable",
                "condition": "restore the original input binding; resume input cannot replace it",
            }
        )
        return report
    checks.append(
        {
            "stage": "input",
            "outcome": "verified",
            "reason": "original_input_retained",
            "condition": "keep workflow input separate from resume input",
        }
    )

    try:
        rows = services.effect_journal.connection.execute(
            "SELECT CASE WHEN LENGTH(i.payload)<=? THEN i.payload ELSE NULL END,"
            "CASE WHEN LENGTH(e.payload)<=? THEN e.payload ELSE NULL END "
            "FROM workflow_effect_invocations i LEFT JOIN workflow_effects e "
            "ON e.identity=i.identity WHERE i.invocation_id=? ORDER BY i.identity LIMIT ?",
            (_MAX_IDENTITY_BYTES, _MAX_RECEIPT_BYTES, handle.invocation_id, _MAX_EFFECTS + 1),
        ).fetchall()
        if len(rows) > _MAX_EFFECTS or any(value is None for row in rows for value in row):
            checks.append(
                {
                    "stage": "actions",
                    "outcome": "incomplete",
                    "reason": "inspection_limit_or_missing_evidence",
                    "condition": "inspect retained evidence through the deployment recovery owner",
                }
            )
        else:
            known, pending = 0, 0
            for identity_json, _receipt_json in rows:
                identity = EffectIdentity.from_value(json.loads(identity_json))
                if (
                    identity.invocation_id != handle.invocation_id
                    or identity.workflow_fingerprint != handle.workflow_fingerprint
                ):
                    raise ValueError("recovery identity is inconsistent")
                receipt = services.effect_journal.get(identity)
                if receipt is None:
                    raise ValueError("recovery receipt is absent")
                if identity.operation.startswith(("call:", "run:")):
                    if receipt.status == "pending":
                        pending += 1
                    else:
                        known += 1
            report["actions"] = {"known": known, "pending": pending, "complete": True}
            checks.append(
                {
                    "stage": "actions",
                    "outcome": "verified",
                    "reason": "pending_actions_present" if pending else "known_actions_retained",
                    "condition": "pending outcomes require normal qualified recovery admission"
                    if pending
                    else "retain original action outcomes and operation identities",
                }
            )
    except Exception:
        checks.append(
            {
                "stage": "actions",
                "outcome": "unavailable",
                "reason": "action_evidence_invalid",
                "condition": "restore the original common action and dependency evidence",
            }
        )
        report["continuation"] = "unavailable"
        return report

    try:
        connection = services.execution_ownership.connection
        clock = (
            "EXTRACT(EPOCH FROM clock_timestamp())"
            if connection.is_postgresql
            else "((julianday('now') - 2440587.5) * 86400.0)"
        )
        row = connection.execute(
            "SELECT engine,fingerprint,expires_at>"
            + clock
            + ",expires_at>"
            + clock
            + "+3600"
            + " FROM invocation_ownership WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        if row is not None and (row[:2] != (handle.engine, handle.workflow_fingerprint) or row[3]):
            raise ValueError("ownership identity is inconsistent")
        report["ownership"] = "held" if row is not None and row[2] else "available"
        checks.append(
            {
                "stage": "ownership",
                "outcome": "observed",
                "reason": "execution_owner_active"
                if report["ownership"] == "held"
                else "execution_owner_available",
                "condition": "normal resume must acquire and retain fenced execution authority",
            }
        )
    except Exception:
        report["ownership"] = "unavailable"
        checks.append(
            {
                "stage": "ownership",
                "outcome": "unavailable",
                "reason": "ownership_evidence_unavailable",
                "condition": "inspect retained authority through the deployment recovery owner",
            }
        )
    report["continuation"] = "common_checked"
    checks.append(
        {
            "stage": "native",
            "outcome": "not_inspected",
            "reason": "native_admission_required",
            "condition": "normal resume must validate the engine's retained native evidence",
        }
    )
    return report
