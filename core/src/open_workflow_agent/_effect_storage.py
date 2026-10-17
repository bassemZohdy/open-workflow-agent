"""No-commit participants for atomic common receipt transactions."""

from __future__ import annotations

import json
from dataclasses import asdict

from .effects import EffectIdentity, EffectReceipt, _receipt_payload
from .errors import WorkflowExecutionError
from .storage import StorageConnection


def insert_completed_receipt(
    connection: StorageConnection, identity: EffectIdentity, receipt: EffectReceipt
) -> None:
    """Insert immutable index and outcome inside the caller's transaction."""
    if receipt.status != "completed":
        raise WorkflowExecutionError("atomic internal outcomes require a completed receipt")
    identity_payload = json.dumps(asdict(identity), sort_keys=True, allow_nan=False)
    payload = json.dumps(
        _receipt_payload(receipt), sort_keys=True, ensure_ascii=False, allow_nan=False
    )
    existing_index = connection.execute(
        "SELECT invocation_id, fingerprint, payload FROM workflow_effect_invocations "
        "WHERE identity = ?",
        (identity.key,),
    ).fetchone()
    if existing_index is not None and (
        existing_index[0] != identity.invocation_id
        or existing_index[1] != identity.workflow_fingerprint
        or EffectIdentity.from_value(json.loads(existing_index[2])) != identity
    ):
        raise WorkflowExecutionError("workflow effect identity index is inconsistent")
    connection.execute(
        """INSERT INTO workflow_effect_invocations(identity, invocation_id, fingerprint, payload)
        VALUES (?, ?, ?, ?) ON CONFLICT(identity) DO NOTHING""",
        (identity.key, identity.invocation_id, identity.workflow_fingerprint, identity_payload),
    )
    connection.execute(
        """INSERT INTO workflow_effects(identity, owner, status, payload)
        VALUES (?, ?, ?, ?) ON CONFLICT(identity) DO NOTHING""",
        (identity.key, receipt.owner, receipt.status, payload),
    )
    row = connection.execute(
        "SELECT owner, status, payload FROM workflow_effects WHERE identity = ?", (identity.key,)
    ).fetchone()
    if (
        row is None
        or row[0] != receipt.owner
        or row[1] != receipt.status
        or json.dumps(json.loads(row[2]), sort_keys=True, allow_nan=False)
        != json.dumps(_receipt_payload(receipt), sort_keys=True, allow_nan=False)
    ):
        raise WorkflowExecutionError("workflow effect completed outcome is inconsistent")
