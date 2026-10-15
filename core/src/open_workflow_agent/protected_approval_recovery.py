"""Read-only approval proof hooks for standard event business receipts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .effects import EffectIdentity, EffectReceipt
from .errors import WorkflowExecutionError
from .persistence import InvocationStore
from .storage import StorageConnection

_APPROVAL_TYPES = frozenset(
    {
        "io.openworkflow.agent.approval.requested",
        "io.openworkflow.agent.approval.decided",
    }
)


def verify_protected_approval_business_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """A skipped emit/listen still authenticates protected request/decision rows.

    The policy verifier reads raw receipt/index/archive evidence, so this hook
    never recursively asks the journal or constructs an approval/event service.
    Legacy and unrelated event receipts retain their existing common contract.
    """
    if receipt.owner != "event" or receipt.status != "completed":
        return
    if identity.operation in {"emit", "listen:event:v1"}:
        events = (receipt.output,)
    elif identity.operation == "listen" and isinstance(receipt.output, list):
        events = tuple(receipt.output)
    else:
        return
    try:
        handle = invocations.get(identity.invocation_id) if invocations is not None else None
        if handle is not None and handle.workflow_fingerprint != identity.workflow_fingerprint:
            raise ValueError
        scope = handle.owner_principal or "anonymous" if handle is not None else "anonymous"
        for value in events:
            _verify_event(connection, invocations, value, scope)
    except Exception:
        raise WorkflowExecutionError(
            "protected approval business evidence is unavailable or inconsistent"
        ) from None


def _verify_event(
    connection: StorageConnection, invocations: InvocationStore | None, value: Any, scope: str
) -> None:
    if not isinstance(value, Mapping) or value.get("type") not in _APPROVAL_TYPES:
        return
    from .events import _make_envelope
    from .protected_approval_store import verify_protected_approval_event

    if not isinstance(value.get("source"), str):
        raise ValueError
    envelope = _make_envelope(value, default_source=value["source"])
    if envelope.as_dict() != value:
        raise ValueError
    verify_protected_approval_event(connection, invocations, envelope, scope)


def protected_approval_event_retained(connection: StorageConnection, event: Any) -> bool:
    """Keep independent human-control evidence, including damaged/orphan hints.

    This is retention, not readiness or authentication. Full proof verification
    still precedes selection/replay. Retention cannot erase a failed proof and
    make a protected control event look like an ordinary legacy event.
    """
    if event.type not in _APPROVAL_TYPES:
        return False
    data = event.data
    if isinstance(data, Mapping) and ("approvalPolicy" in data or "approvalProfile" in data):
        return True
    prefix = "owa_approvals." if connection.is_postgresql else ""
    for suffix in ("protected_approval_requests", "protected_approval_audit"):
        table = prefix + suffix
        present = (
            connection.execute("SELECT to_regclass(?)", (table,)).fetchone()[0] is not None
            if connection.is_postgresql
            else connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (suffix,)
            ).fetchone()
            is not None
        )
        if (
            present
            and connection.execute(
                "SELECT 1 FROM " + table + " WHERE approval_id=? LIMIT 1", (event.subject,)
            ).fetchone()
            is not None
        ):
            return True
    return False


def protected_approval_invocation_retained(
    connection: StorageConnection, invocation_id: str
) -> bool:
    """An independent request remains history even without archive/index rows."""
    table = ("owa_approvals." if connection.is_postgresql else "") + "protected_approval_requests"
    present = (
        connection.execute("SELECT to_regclass(?)", (table,)).fetchone()[0] is not None
        if connection.is_postgresql
        else connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )
    return bool(
        present
        and connection.execute(
            "SELECT 1 FROM " + table + " WHERE invocation_id=? LIMIT 1", (invocation_id,)
        ).fetchone()
        is not None
    )
