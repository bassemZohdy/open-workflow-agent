"""Private immutable original authority, action decisions and outcome audit seals.

These are common trust evidence, never native checkpoints or bearer credentials.
All mutation participants use the caller's real transaction and execution fence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ._effect_storage import insert_completed_receipt
from .effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
    _receipt_payload,
    effect_request_digest,
)
from .errors import InvocationStateConflict, InvocationStateError, OwaError, WorkflowExecutionError
from .execution_authority import ExecutionAuthority, current_authority
from .lifecycle import VALID_INVOCATION_TRANSITIONS
from .ownership import ExecutionLease, execution_write_guard, owned_write_transaction
from .persistence import (
    ExecutionHandle,
    InvocationStore,
    _decode_payload,
)
from .recovery_definitions import (
    MAX_ARCHIVE_BYTES,
    MAX_PROFILE_BYTES,
    _expected_receipt,
    _identity,
    admission_identity,
)
from .security import (
    ApiKeySecurityProfile,
    BearerSecurityProfile,
    ExecutionAuthorizationConfig,
    SecurityConfig,
    authorize,
    static_principal,
)
from .storage import StorageConnection

if TYPE_CHECKING:
    from ._schedule_storage import ScheduleClaim
    from ._schedule_store import ScheduleRecord
    from .recovery_definitions import RecoveryDefinitionArchive
    from .workflow import WorkflowPlan

AUTHORITY_OPERATION = "policy:execution-authority:v1"
DECISION_OPERATION = "policy:execution-authorization:v1"
SEAL_OPERATION = "policy:execution-authorization-seal:v1"
INTENT_OPERATION = "policy:execution-authorization-intent:v1"
PROFILE = "owa/execution-authorization/v1"
MAX_RECORD_BYTES = 262_144
MAX_DECISIONS = 512
MAX_BUSINESS_DECISIONS = 128
MAX_AUDITED_RECEIPT_BYTES = 67_108_864


class ExecutionAuthorizationError(OwaError):
    code = "execution_authorization_denied"
    status_code = 403


class AuthorizationOutcomeUnavailable(WorkflowExecutionError):
    """The business action happened but its finite audit outcome cannot commit."""


def _denied(reason: str) -> ExecutionAuthorizationError:
    return ExecutionAuthorizationError("execution authorization denied", details={"reason": reason})


def _invalid(reason: str) -> InvocationStateError:
    return InvocationStateError(
        "original execution authority is unavailable or inconsistent", details={"reason": reason}
    )


def _encoded(value: Any, *, limit: int = MAX_RECORD_BYTES) -> str:
    effect_request_digest(PROFILE, value)
    result = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(result.encode("utf-8")) > limit:
        raise _invalid("authority_limit")
    return result


def _parsed(value: Any, *, limit: int = MAX_RECORD_BYTES) -> Any:
    if type(value) is not str or len(value.encode("utf-8")) > limit:
        raise _invalid("authority_limit")

    def distinct(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for name, item in items:
            if name in result:
                raise ValueError("duplicate authority field")
            result[name] = item
        return result

    try:
        result = json.loads(value, object_pairs_hook=distinct)
        _encoded(result, limit=limit)
        return result
    except OwaError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise _invalid("authority_record_invalid") from None


def _authority_descriptor(value: Any) -> ExecutionAuthority:
    try:
        return ExecutionAuthority.from_value(value)
    except (ValueError, TypeError, KeyError):
        raise _invalid("authority_descriptor_changed") from None


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value, limit=MAX_ARCHIVE_BYTES).encode()).hexdigest()


def _effect(identity: EffectIdentity) -> dict[str, Any]:
    return {
        "invocation_id": identity.invocation_id,
        "workflow_fingerprint": identity.workflow_fingerprint,
        "task_reference": identity.task_reference,
        "occurrence": [[name, visit] for name, visit in identity.occurrence],
        "operation": identity.operation,
    }


def authority_identity(handle: ExecutionHandle) -> EffectIdentity:
    return replace(admission_identity(handle), operation=AUTHORITY_OPERATION)


_authority_identity = authority_identity


def decision_identity(identity: EffectIdentity, action: str, resource: str) -> EffectIdentity:
    return replace(
        identity,
        operation=DECISION_OPERATION + ":" + _digest([identity.operation, action, resource]),
    )


def requires_authorization_seal(identity: EffectIdentity) -> bool:
    return identity.operation.startswith(("call:", "run:")) or identity.operation in {
        "emit",
        "listen",
    }


def _table_exists(connection: StorageConnection, table: str) -> bool:
    if connection.is_postgresql:
        return connection.execute("SELECT to_regclass(?)", (table,)).fetchone()[0] is not None
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _authority_hint(connection: StorageConnection, identity: EffectIdentity) -> bool:
    if _table_exists(connection, "workflow_effects"):
        authority = replace(
            identity,
            task_reference="/do",
            occurrence=(("/do", 1),),
            operation=AUTHORITY_OPERATION,
        )
        keys = (
            authority.key,
            replace(authority, operation="policy:execution-authority-origin:v1").key,
            replace(identity, operation=SEAL_OPERATION).key,
            intent_identity(identity).key,
        )
        if (
            connection.execute(
                "SELECT 1 FROM workflow_effects WHERE identity IN (?,?,?,?) LIMIT 1", keys
            ).fetchone()
            is not None
        ):
            return True
    if _table_exists(connection, "recovery_invocation_definitions"):
        binding = _bounded(
            connection,
            "recovery_invocation_definitions",
            "invocation_id",
            identity.invocation_id,
            limit=MAX_PROFILE_BYTES,
        )
        if type(binding) is dict and (
            "execution_authority_digest" in binding
            or binding.get("runtime_profile", {})
            .get("policy", {})
            .get("execution_authorization", {})
            .get("enabled")
            is True
        ):
            return True
    if (
        _table_exists(connection, "execution_authorities")
        and connection.execute(
            "SELECT 1 FROM execution_authorities WHERE invocation_id=?", (identity.invocation_id,)
        ).fetchone()
        is not None
    ):
        return True
    if (
        _table_exists(connection, "workflow_effect_invocations")
        and connection.execute(
            "SELECT 1 FROM workflow_effect_invocations WHERE invocation_id=? "
            "AND (payload LIKE ? OR payload LIKE ? OR payload LIKE ?) LIMIT 1",
            (
                identity.invocation_id,
                "%policy:execution-authority:v1%",
                "%policy:execution-authorization%",
                "%policy:execution-authority-origin:v1%",
            ),
        ).fetchone()
        is not None
    ):
        return True
    for table in (
        "execution_authorization_decisions",
        "execution_authorization_intents",
        "execution_authorization_outcomes",
    ):
        if (
            _table_exists(connection, table)
            and connection.execute(
                f"SELECT 1 FROM {table} WHERE invocation_id=? LIMIT 1",
                (identity.invocation_id,),
            ).fetchone()
            is not None
        ):
            return True
    return False


def intent_identity(identity: EffectIdentity) -> EffectIdentity:
    return replace(identity, operation=INTENT_OPERATION + ":" + _digest(identity.operation))


def _bounded(
    connection: StorageConnection,
    table: str,
    column: str,
    key: str,
    *,
    limit: int = MAX_RECORD_BYTES,
) -> Any:
    length = (
        "octet_length(payload)" if connection.is_postgresql else "length(CAST(payload AS BLOB))"
    )
    row = connection.execute(
        f"SELECT CASE WHEN {length}<=? THEN payload ELSE NULL END FROM {table} WHERE {column}=?",
        (limit, key),
    ).fetchone()
    if row is None:
        return None
    return _parsed(row[0], limit=limit)


def _read_receipt(
    connection: StorageConnection,
    identity: EffectIdentity,
    expected: EffectReceipt,
    *,
    limit: int = MAX_RECORD_BYTES,
) -> None:
    payload = _bounded(connection, "workflow_effects", "identity", identity.key, limit=limit)
    index = _bounded(connection, "workflow_effect_invocations", "identity", identity.key)
    row = connection.execute(
        "SELECT owner,status FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()
    indexed = connection.execute(
        "SELECT invocation_id,fingerprint FROM workflow_effect_invocations WHERE identity=?",
        (identity.key,),
    ).fetchone()
    if (
        payload != _receipt_payload(expected)
        or index is None
        or EffectIdentity.from_value(index) != identity
        or row is None
        or tuple(row) != (expected.owner, expected.status)
        or indexed is None
        or tuple(indexed) != (identity.invocation_id, identity.workflow_fingerprint)
    ):
        raise _invalid("authorization_receipt_missing_or_changed")


def _known_receipt(
    connection: StorageConnection,
    identity: EffectIdentity,
    *,
    limit: int = MAX_AUDITED_RECEIPT_BYTES,
) -> EffectReceipt:
    saved = _bounded(connection, "workflow_effects", "identity", identity.key, limit=limit)
    if type(saved) is not dict:
        raise _invalid("authorization_business_receipt_missing")
    try:
        if saved.get("child_invocation") is not None:
            from .effects import ChildInvocationDependency

            saved["child_invocation"] = ChildInvocationDependency.from_value(
                saved["child_invocation"]
            )
        if saved.get("external_outcome") is not None:
            from .external_recovery import ExternalOutcomeDependency

            saved["external_outcome"] = ExternalOutcomeDependency.from_value(
                saved["external_outcome"]
            )
        return EffectReceipt(**saved)
    except (TypeError, ValueError, KeyError):
        raise _invalid("authorization_business_receipt_invalid") from None


def _record(
    connection: StorageConnection, table: str, column: str, key: str
) -> dict[str, Any] | None:
    value = _bounded(connection, table, column, key)
    if value is None:
        return None
    row = connection.execute(f"SELECT digest FROM {table} WHERE {column}=?", (key,)).fetchone()
    if type(value) is not dict or row is None or row[0] != _digest(value):
        raise _invalid("authorization_record_changed")
    return value


def _archive_evidence(
    connection: StorageConnection, invocations: InvocationStore, handle: ExecutionHandle
) -> dict[str, Any]:
    binding = _bounded(
        connection,
        "recovery_invocation_definitions",
        "invocation_id",
        handle.invocation_id,
        limit=MAX_PROFILE_BYTES,
    )
    source = _bounded(
        connection,
        "recovery_definitions",
        "fingerprint",
        handle.workflow_fingerprint,
        limit=MAX_ARCHIVE_BYTES,
    )
    original = _bounded(
        connection,
        "invocation_inputs",
        "invocation_id",
        handle.invocation_id,
        limit=MAX_ARCHIVE_BYTES,
    )
    # JSON null input is valid; distinguish it from an absent input row.
    input_row = connection.execute(
        "SELECT fingerprint FROM invocation_inputs WHERE invocation_id=?", (handle.invocation_id,)
    ).fetchone()
    current = invocations.get(handle.invocation_id)
    if (
        type(binding) is not dict
        or source is None
        or input_row is None
        or input_row[0] != handle.workflow_fingerprint
        or current is None
        or _identity(current) != _identity(handle)
        or binding.get("identity") != _identity(handle)
        or binding.get("definition_digest") != _digest(source)
        or binding.get("input_digest") != _digest(original)
    ):
        raise _invalid("authority_original_binding_changed")
    row = connection.execute(
        "SELECT fingerprint,digest FROM recovery_invocation_definitions WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    source_row = connection.execute(
        "SELECT digest FROM recovery_definitions WHERE fingerprint=?",
        (handle.workflow_fingerprint,),
    ).fetchone()
    if (
        row is None
        or tuple(row) != (handle.workflow_fingerprint, _digest(binding))
        or source_row is None
        or source_row[0] != _digest(source)
    ):
        raise _invalid("authority_original_binding_changed")
    _read_receipt(connection, admission_identity(handle), _expected_receipt(binding))
    return {
        "identity": _identity(handle),
        "definition_digest": binding["definition_digest"],
        "input_digest": binding["input_digest"],
        "authority_digest": binding.get("execution_authority_digest"),
    }


def _authority_receipt(value: dict[str, Any]) -> EffectReceipt:
    output = {"profile": PROFILE, "authority_digest": _digest(value)}
    return EffectReceipt(
        effect_request_digest(AUTHORITY_OPERATION, output),
        "execution-authority",
        status="completed",
        output=output,
    )


def _decision_receipt(value: dict[str, Any]) -> EffectReceipt:
    output = {"profile": PROFILE, "decision_digest": _digest(value), "audit": value["audit"]}
    return EffectReceipt(
        effect_request_digest(DECISION_OPERATION, output),
        "execution-authorization",
        status="completed",
        output=output,
    )


def _seal_receipt(value: dict[str, Any]) -> EffectReceipt:
    output = {"profile": PROFILE, "seal_digest": _digest(value)}
    return EffectReceipt(
        effect_request_digest(SEAL_OPERATION, output),
        "execution-authorization",
        status="completed",
        output=output,
    )


def _intent_receipt(value: dict[str, Any]) -> EffectReceipt:
    output = {"profile": PROFILE, "intent_digest": _digest(value)}
    return EffectReceipt(
        effect_request_digest(INTENT_OPERATION, output),
        "execution-authorization",
        status="completed",
        output=output,
    )


def _read_intent(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
    *,
    authority_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = _record(connection, "execution_authorization_intents", "business_key", identity.key)
    handle = invocations.get(identity.invocation_id)
    if handle is None or handle.workflow_fingerprint != identity.workflow_fingerprint:
        raise _invalid("authorization_invocation_missing")
    authority = (
        authority_record
        if authority_record is not None
        else _read_authority(connection, invocations, handle)
    )
    if (
        value is None
        or set(value)
        != {"profile", "business_identity", "authority_digest", "pending_receipt", "generation"}
        or value["profile"] != PROFILE
        or value["business_identity"] != _effect(identity)
        or authority is None
        or value["authority_digest"] != _digest(authority)
        or type(value["generation"]) is not int
        or value["generation"] < 1
    ):
        raise _invalid("authorization_intent_missing_or_changed")
    original = EffectReceipt(**value["pending_receipt"])
    if (
        original.status != "pending"
        or original.output is not None
        or original.error is not None
        or original.child_invocation is not None
        or original.external_outcome is not None
        or original.request_digest != receipt.request_digest
        or original.owner != receipt.owner
        or (original.operation_id is not None and original.operation_id != receipt.operation_id)
    ):
        raise _invalid("authorization_intent_changed")
    row = connection.execute(
        "SELECT invocation_id,fingerprint FROM execution_authorization_intents "
        "WHERE business_key=?",
        (identity.key,),
    ).fetchone()
    if row is None or tuple(row) != (identity.invocation_id, identity.workflow_fingerprint):
        raise _invalid("authorization_intent_changed")
    _read_receipt(connection, intent_identity(identity), _intent_receipt(value))
    return value


def verify_execution_authorization_intent_receipt(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    rows = connection.execute(
        "SELECT business_key FROM execution_authorization_intents WHERE invocation_id=? LIMIT ?",
        (identity.invocation_id, MAX_DECISIONS + 1),
    ).fetchall()
    if len(rows) > MAX_DECISIONS:
        raise _invalid("authority_limit")
    for row in rows:
        value = _record(connection, "execution_authorization_intents", "business_key", row[0])
        if value is None:
            raise _invalid("authorization_intent_missing_or_changed")
        business = EffectIdentity.from_value(value["business_identity"])
        if intent_identity(business) == identity:
            current = _known_receipt(connection, business)
            _read_receipt(connection, business, current, limit=MAX_AUDITED_RECEIPT_BYTES)
            _read_intent(connection, invocations, business, current)
            if receipt != _intent_receipt(value):
                raise _invalid("authorization_intent_changed")
            return
    raise _invalid("authorization_intent_missing_or_changed")


def _read_authority(
    connection: StorageConnection,
    invocations: InvocationStore,
    handle: ExecutionHandle,
    *,
    ancestors: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    if handle.invocation_id in ancestors or len(ancestors) >= 64:
        raise _invalid("authority_origin_cycle")
    ancestors = ancestors | {handle.invocation_id}
    value = _record(connection, "execution_authorities", "invocation_id", handle.invocation_id)
    identity = _authority_identity(handle)
    if value is None:
        original = _bounded(
            connection,
            "recovery_invocation_definitions",
            "invocation_id",
            handle.invocation_id,
            limit=MAX_PROFILE_BYTES,
        )
        if original is not None:
            _archive_evidence(connection, invocations, handle)
            if (
                original.get("runtime_profile", {})
                .get("policy", {})
                .get("execution_authorization", {})
                .get("enabled")
                is True
            ):
                raise _invalid("authority_binding_missing")
        if original is not None and "execution_authority_digest" in original:
            raise _invalid("authority_binding_missing")
        if (
            connection.execute(
                "SELECT 1 FROM workflow_effects WHERE identity=? UNION ALL SELECT 1 "
                "FROM workflow_effect_invocations WHERE identity=? LIMIT 1",
                (identity.key, identity.key),
            ).fetchone()
            is not None
        ):
            raise _invalid("authority_binding_missing")
        if _authority_hint(connection, identity):
            raise _invalid("authority_binding_missing")
        return None
    if (
        set(value)
        != {"profile", "original", "authority", "policy", "policy_digest", "candidate_digest"}
        or value["profile"] != PROFILE
        or value["original"] != _archive_evidence(connection, invocations, handle)
        or value["policy_digest"] != _digest(value["policy"])
    ):
        raise _invalid("authority_binding_changed")
    row = connection.execute(
        "SELECT fingerprint FROM execution_authorities WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    if row is None or row[0] != handle.workflow_fingerprint:
        raise _invalid("authority_binding_changed")
    authority = _authority_descriptor(value["authority"])
    if handle.owner_principal != authority.principal.identity or value["original"][
        "authority_digest"
    ] != _digest(authority.as_value()):
        raise _invalid("authority_owner_changed")
    if value["candidate_digest"] is not None:
        candidate = _record(
            connection,
            "execution_authority_candidates",
            "candidate_key",
            "invocation:" + handle.invocation_id,
        )
        if (
            candidate is None
            or _digest(candidate) != value["candidate_digest"]
            or candidate["authority"] != value["authority"]
            or candidate["policy"] != value["policy"]
            or candidate["binding"]["identity"] != _identity(handle)
            or candidate["binding"]["input_digest"] != value["original"]["input_digest"]
        ):
            raise _invalid("authority_origin_missing_or_changed")
        _verify_origin(connection, invocations, handle, candidate, ancestors=ancestors)
    _read_receipt(connection, identity, _authority_receipt(value))
    return value


def _verify_origin(
    connection: StorageConnection,
    invocations: InvocationStore,
    handle: ExecutionHandle,
    candidate: dict[str, Any],
    *,
    ancestors: frozenset[str] = frozenset(),
) -> None:
    binding = candidate["binding"]
    fields = {"kind", "identity", "input_digest"}
    if binding["kind"] == "child":
        from .effects import ChildInvocationDependency

        if ChildInvocationDependency._provenance(invocations, handle.invocation_id) is None:
            raise _invalid("authority_parent_changed")
        fields |= {"parent_invocation_id", "parent_authority_digest", "child_binding_digest"}
    elif binding["kind"] == "scheduled":
        fields |= {"schedule_id", "slot_key", "schedule_authority_digest", "slot_binding_digest"}
    elif binding["kind"] != "invocation":
        raise _invalid("authority_origin_changed")
    if set(binding) != fields or binding["identity"] != _identity(handle):
        raise _invalid("authority_origin_changed")
    if binding["kind"] == "child":
        parent = invocations.get(binding["parent_invocation_id"])
        if parent is None or handle.parent_invocation_id != parent.invocation_id:
            raise _invalid("authority_parent_changed")
        proof = _read_authority(connection, invocations, parent, ancestors=ancestors)
        rows = connection.execute(
            "SELECT digest FROM child_workflow_bindings WHERE child_id=? AND parent_id=?",
            (handle.invocation_id, parent.invocation_id),
        ).fetchall()
        if (
            proof is None
            or _digest(proof) != binding["parent_authority_digest"]
            or len(rows) != 1
            or rows[0][0] != binding["child_binding_digest"]
            or proof["authority"] != candidate["authority"]
        ):
            raise _invalid("authority_parent_changed")
    elif binding["kind"] == "scheduled":
        schedule = _record(
            connection,
            "execution_authority_candidates",
            "candidate_key",
            "schedule:" + binding["schedule_id"],
        )
        definition = _bounded(
            connection,
            "schedule_definitions",
            "schedule_id",
            binding["schedule_id"],
            limit=MAX_ARCHIVE_BYTES,
        )
        slot = connection.execute(
            "SELECT invocation_id FROM schedule_slots WHERE slot_key=?", (binding["slot_key"],)
        ).fetchone()
        saved = _bounded(
            connection,
            "schedule_bindings",
            "slot_key",
            binding["slot_key"],
            limit=MAX_PROFILE_BYTES,
        )
        if (
            schedule is None
            or _digest(schedule) != binding["schedule_authority_digest"]
            or schedule["authority"] != candidate["authority"]
            or schedule["policy"] != candidate["policy"]
            or type(definition) is not dict
            or _digest(definition) != schedule["binding"]["definition_digest"]
            or definition.get("execution_authority_digest") != _digest(candidate["authority"])
            or slot is None
            or slot[0] != handle.invocation_id
            or saved is None
            or _digest(saved) != binding["slot_binding_digest"]
        ):
            raise _invalid("authority_schedule_changed")


def verify_execution_authority_receipt(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    handle = invocations.get(identity.invocation_id)
    if handle is None or identity != _authority_identity(handle):
        raise _invalid("authority_identity_changed")
    value = _read_authority(connection, invocations, handle)
    if value is None or receipt != _authority_receipt(value):
        raise _invalid("authority_binding_missing")


def _read_decision(
    connection: StorageConnection,
    invocations: InvocationStore,
    key: str,
    *,
    authority_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = _record(connection, "execution_authorization_decisions", "decision_key", key)
    if (
        value is None
        or set(value)
        != {"profile", "business_identity", "authority_digest", "request_digest", "audit"}
        or value["profile"] != PROFILE
    ):
        raise _invalid("authorization_decision_missing")
    business = EffectIdentity.from_value(value["business_identity"])
    handle = invocations.get(business.invocation_id)
    if handle is None or handle.workflow_fingerprint != business.workflow_fingerprint:
        raise _invalid("authorization_invocation_missing")
    authority = (
        authority_record
        if authority_record is not None
        else _read_authority(connection, invocations, handle)
    )
    audit = value["audit"]
    if (
        authority is None
        or value["authority_digest"] != _digest(authority)
        or type(audit) is not dict
        or set(audit)
        != {
            "principal",
            "action",
            "resource",
            "decision",
            "policy_id",
            "policy_version",
            "policy_digest",
            "policy_enforced",
            "generation",
        }
        or audit["principal"] != _authority_descriptor(authority["authority"]).principal.identity
        or audit["decision"] != "allow"
        or audit["policy_digest"] != authority["policy_digest"]
        or audit["policy_id"] != authority["policy"]["id"]
        or audit["policy_version"] != authority["policy"]["version"]
        or audit["policy_enforced"] != authority["policy"]["enabled"]
        or type(audit["generation"]) is not int
        or audit["generation"] < 1
    ):
        raise _invalid("authorization_decision_changed")
    identity = decision_identity(business, audit["action"], audit["resource"])
    if (
        type(audit["action"]) is not str
        or not audit["action"]
        or len(audit["action"].encode()) > 128
        or type(audit["resource"]) is not str
        or not audit["resource"]
        or len(audit["resource"].encode()) > 4096
        or any(ord(item) < 32 for item in audit["action"] + audit["resource"])
        or type(value["request_digest"]) is not str
        or len(value["request_digest"]) != 64
        or any(item not in "0123456789abcdef" for item in value["request_digest"])
        or authority["policy"]["enabled"] is not True
        or not authorize(
            _authority_descriptor(authority["authority"]).principal,
            ExecutionAuthorizationConfig.model_validate(authority["policy"]).policy,
            action=audit["action"],
            resource=audit["resource"],
        )
    ):
        raise _invalid("authorization_decision_changed")
    if identity.key != key:
        raise _invalid("authorization_decision_changed")
    row = connection.execute(
        "SELECT business_key,invocation_id,fingerprint FROM "
        "execution_authorization_decisions WHERE decision_key=?",
        (key,),
    ).fetchone()
    if row is None or tuple(row) != (
        business.key,
        business.invocation_id,
        business.workflow_fingerprint,
    ):
        raise _invalid("authorization_decision_changed")
    _read_receipt(connection, identity, _decision_receipt(value))
    return value


def verify_execution_authorization_receipt(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    value = _read_decision(connection, invocations, identity.key)
    business = EffectIdentity.from_value(value["business_identity"])
    if identity != decision_identity(
        business, value["audit"]["action"], value["audit"]["resource"]
    ) or receipt != _decision_receipt(value):
        raise _invalid("authorization_decision_changed")


def verify_execution_authorization_business_receipt(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
    *,
    authority_record: dict[str, Any] | None = None,
) -> None:
    if not requires_authorization_seal(identity):
        return
    if not _table_exists(connection, "execution_authorities"):
        if _authority_hint(connection, identity):
            raise _invalid("authority_binding_missing")
        return
    handle = invocations.get(identity.invocation_id)
    if handle is None:
        if not _authority_hint(connection, identity):
            return
        raise _invalid("authorization_invocation_missing")
    if handle.workflow_fingerprint != identity.workflow_fingerprint:
        raise _invalid("authorization_invocation_changed")
    authority = (
        authority_record
        if authority_record is not None
        else _read_authority(connection, invocations, handle)
    )
    value = _record(connection, "execution_authorization_outcomes", "business_key", identity.key)
    if authority is None and _authority_hint(connection, identity):
        raise _invalid("authority_binding_missing")
    if authority is None and value is None:
        if (
            connection.execute(
                "SELECT 1 FROM execution_authorization_intents WHERE business_key=? "
                "UNION ALL SELECT 1 FROM execution_authorization_decisions WHERE "
                "business_key=? LIMIT 1",
                (identity.key, identity.key),
            ).fetchone()
            is not None
        ):
            raise _invalid("authority_binding_missing")
        return
    if authority is not None and not authority["policy"]["enabled"]:
        if value is not None:
            raise _invalid("authorization_unexpected_seal")
        return
    if authority is not None:
        _read_receipt(
            connection, identity, receipt, limit=authority["policy"]["max_business_receipt_bytes"]
        )
        intent = _record(
            connection, "execution_authorization_intents", "business_key", identity.key
        )
        if (
            intent is not None
            or receipt.status == "pending"
            or (value is not None and value.get("intent_digest") is not None)
        ):
            _read_intent(connection, invocations, identity, receipt, authority_record=authority)
        if receipt.status == "pending":
            return
    if (
        authority is None
        or value is None
        or set(value)
        != {
            "profile",
            "business_identity",
            "authority_digest",
            "receipt_digest",
            "decisions",
            "intent_digest",
        }
        or value["profile"] != PROFILE
        or value["business_identity"] != _effect(identity)
        or value["authority_digest"] != _digest(authority)
        or value["receipt_digest"] != ReceiptDependency.capture(identity, receipt).receipt_digest
        or value["intent_digest"] != (_digest(intent) if intent is not None else None)
    ):
        raise _invalid("authorization_outcome_seal_missing_or_changed")
    row = connection.execute(
        "SELECT invocation_id,fingerprint FROM execution_authorization_outcomes "
        "WHERE business_key=?",
        (identity.key,),
    ).fetchone()
    if row is None or tuple(row) != (identity.invocation_id, identity.workflow_fingerprint):
        raise _invalid("authorization_outcome_seal_changed")
    dependencies = value["decisions"]
    if (
        type(dependencies) is not list
        or not 1 <= len(dependencies) <= MAX_BUSINESS_DECISIONS
        or any(
            type(item) is not dict
            or set(item) != {"key", "receipt_digest"}
            or type(item["key"]) is not str
            for item in dependencies
        )
    ):
        raise _invalid("authorization_outcome_seal_changed")
    keys = [item["key"] for item in dependencies]
    if keys != sorted(set(keys)):
        raise _invalid("authorization_outcome_seal_changed")
    current = [
        row[0]
        for row in connection.execute(
            "SELECT decision_key FROM execution_authorization_decisions WHERE "
            "business_key=? ORDER BY decision_key LIMIT ?",
            (identity.key, MAX_BUSINESS_DECISIONS + 1),
        ).fetchall()
    ]
    if keys != current:
        raise _invalid("authorization_decision_missing")
    for dependency in dependencies:
        decision = _read_decision(
            connection, invocations, dependency["key"], authority_record=authority
        )
        decision_id = decision_identity(
            identity, decision["audit"]["action"], decision["audit"]["resource"]
        )
        if (
            decision["business_identity"] != _effect(identity)
            or ReceiptDependency.capture(decision_id, _decision_receipt(decision)).receipt_digest
            != dependency["receipt_digest"]
        ):
            raise _invalid("authorization_decision_changed")
    _read_receipt(connection, replace(identity, operation=SEAL_OPERATION), _seal_receipt(value))


def verify_execution_authorization_seal_receipt(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    value = _bounded(connection, "workflow_effects", "identity", identity.key)
    if value is None or receipt.status != "completed":
        raise _invalid("authorization_outcome_seal_missing_or_changed")
    rows = connection.execute(
        "SELECT business_key FROM execution_authorization_outcomes WHERE invocation_id=? LIMIT ?",
        (identity.invocation_id, MAX_DECISIONS + 1),
    ).fetchall()
    for row in rows:
        outcome = _record(connection, "execution_authorization_outcomes", "business_key", row[0])
        if outcome is None:
            raise _invalid("authorization_outcome_seal_missing_or_changed")
        business = EffectIdentity.from_value(outcome["business_identity"])
        if replace(business, operation=SEAL_OPERATION) == identity:
            handle = invocations.get(business.invocation_id)
            if handle is None:
                raise _invalid("authorization_invocation_missing")
            authority = _read_authority(connection, invocations, handle)
            if authority is None:
                raise _invalid("authority_binding_missing")
            known = _known_receipt(
                connection, business, limit=authority["policy"]["max_business_receipt_bytes"]
            )
            verify_execution_authorization_business_receipt(
                connection, invocations, business, known
            )
            if receipt != _seal_receipt(outcome):
                raise _invalid("authorization_outcome_seal_changed")
            return
    raise _invalid("authorization_outcome_seal_missing_or_changed")


class _ProofInvocationReader(InvocationStore):
    """Constructor-free, bounded SELECT reader for the independent supervisor."""

    def __init__(self, connection: StorageConnection) -> None:
        self.connection = connection

    def get(self, invocation_id: str) -> ExecutionHandle | None:
        length = (
            "octet_length(payload)"
            if self.connection.is_postgresql
            else "length(CAST(payload AS BLOB))"
        )
        row = self.connection.execute(
            f"SELECT CASE WHEN {length}<=? THEN payload ELSE NULL END,status "
            "FROM invocations WHERE invocation_id=?",
            (MAX_ARCHIVE_BYTES, invocation_id),
        ).fetchone()
        if row is None:
            return None
        try:
            handle = ExecutionHandle(**_decode_payload(row[0]))
            if (
                handle.invocation_id != invocation_id
                or handle.status != row[1]
                or handle.status not in VALID_INVOCATION_TRANSITIONS
            ):
                raise ValueError
            handle._persisted_payload = row[0]
            return handle
        except (ValueError, TypeError, KeyError):
            raise _invalid("authorization_invocation_changed") from None

    def read_input(self, handle: ExecutionHandle) -> Any:
        row = self.connection.execute(
            "SELECT fingerprint FROM invocation_inputs WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        if row is None or row[0] != handle.workflow_fingerprint:
            raise _invalid("authority_original_binding_changed")
        return _bounded(
            self.connection,
            "invocation_inputs",
            "invocation_id",
            handle.invocation_id,
            limit=MAX_ARCHIVE_BYTES,
        )


def verify_authorized_business_snapshot(
    connection: StorageConnection,
    identity: EffectIdentity,
    receipt: EffectReceipt,
    *,
    creator: dict[str, Any] | None = None,
) -> None:
    """Verify launch evidence without service construction, adoption or current claims.

    Current deployment policy admission remains the manager's separate gate.
    This inspector verifies the retained original authority and outcome proof.
    """

    if not _authority_hint(connection, identity):
        return
    if not _table_exists(connection, "invocations") or not _table_exists(
        connection, "execution_authorities"
    ):
        raise _invalid("authority_binding_missing")
    reader = _ProofInvocationReader(connection)
    handle = reader.get(identity.invocation_id)
    if handle is None or handle.workflow_fingerprint != identity.workflow_fingerprint:
        raise _invalid("authorization_invocation_missing")
    if creator is not None and any(
        creator.get(name) != item for name, item in _identity(handle).items()
    ):
        raise _invalid("authorization_invocation_changed")
    if _read_authority(connection, reader, handle) is None:
        raise _invalid("authority_binding_missing")
    verify_execution_authorization_business_receipt(connection, reader, identity, receipt)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    identity: EffectIdentity
    receipt: EffectReceipt
    audit: dict[str, Any]


class ExecutionAuthorizationStore:
    def __init__(
        self,
        journal: EffectJournal,
        invocations: InvocationStore,
        archive: RecoveryDefinitionArchive,
        *,
        config_provider: Callable[[], ExecutionAuthorizationConfig],
        security_provider: Callable[[], SecurityConfig],
    ) -> None:
        self.journal, self.invocations, self.archive = journal, invocations, archive
        self.connection = journal.connection
        self.config_provider, self.security_provider = config_provider, security_provider
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_authorities (invocation_id TEXT "
            "PRIMARY KEY,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,digest "
            "TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_authority_candidates "
            "(candidate_key TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT "
            "NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_authorization_decisions "
            "(decision_key TEXT PRIMARY KEY,business_key TEXT NOT "
            "NULL,invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,payload "
            "TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS execution_authorization_invocations ON "
            "execution_authorization_decisions(invocation_id,business_key)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_authorization_outcomes "
            "(business_key TEXT PRIMARY KEY,invocation_id TEXT NOT "
            "NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT "
            "NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_authorization_intents "
            "(business_key TEXT PRIMARY KEY,invocation_id TEXT NOT "
            "NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT "
            "NULL)"
        )
        self.connection.commit()

    def _policy(self) -> dict[str, Any]:
        value = self.config_provider().model_dump(mode="json")
        _encoded(value, limit=65_536)
        return value

    def expected_authority_digest(self, handle: ExecutionHandle) -> str | None:
        original = _bounded(
            self.connection,
            "recovery_invocation_definitions",
            "invocation_id",
            handle.invocation_id,
            limit=MAX_PROFILE_BYTES,
        )
        if original is not None:
            value = original.get("execution_authority_digest")
            if value is not None and type(value) is not str:
                raise _invalid("authority_binding_changed")
            return value
        selected = self.reserved_invocation(handle) or current_authority()
        self.validate_authority(selected)
        return _digest(selected.as_value()) if selected is not None else None

    def expected_schedule_authority_digest(
        self, record: ScheduleRecord | None = None, *, authority: ExecutionAuthority | None = None
    ) -> str | None:
        selected = authority or current_authority()
        self.validate_authority(selected)
        return _digest(selected.as_value()) if selected is not None else None

    def authority_receipt(self, handle: ExecutionHandle) -> AuthorizationDecision | None:
        self.verify_invocation(handle)
        value = _read_authority(self.connection, self.invocations, handle)
        if value is None:
            return None
        principal = _authority_descriptor(value["authority"]).principal.identity
        return AuthorizationDecision(
            authority_identity(handle),
            _authority_receipt(value),
            {
                "principal": principal,
                "policy_id": value["policy"]["id"],
                "policy_version": value["policy"]["version"],
            },
        )

    def intent_receipt(self, identity: EffectIdentity) -> AuthorizationDecision | None:
        handle = self.invocations.get(identity.invocation_id)
        if handle is None:
            if self._unbound_legacy(identity):
                return None
            raise _invalid("authorization_invocation_missing")
        authority = _read_authority(self.connection, self.invocations, handle)
        if authority is None:
            if _authority_hint(self.connection, identity):
                raise _invalid("authority_binding_missing")
            return None
        if not authority["policy"]["enabled"]:
            return None
        receipt = _known_receipt(
            self.connection, identity, limit=authority["policy"]["max_business_receipt_bytes"]
        )
        value = _read_intent(
            self.connection, self.invocations, identity, receipt, authority_record=authority
        )
        return AuthorizationDecision(
            intent_identity(identity), _intent_receipt(value), {"generation": value["generation"]}
        )

    def has_protected_business(self, identity: EffectIdentity) -> bool:
        """Original opt-in decides completion fencing; missing proof cannot downgrade."""
        handle = self.invocations.get(identity.invocation_id)
        if handle is None or handle.workflow_fingerprint != identity.workflow_fingerprint:
            raise _invalid("authorization_invocation_missing")
        self.verify_invocation(handle)
        value = _read_authority(self.connection, self.invocations, handle)
        return value is not None and value["policy"]["enabled"] is True

    def validate_authority(self, authority: ExecutionAuthority | None) -> None:
        if authority is None:
            if self.config_provider().enabled:
                raise _denied("authority_required")
            return
        try:
            profile = self.security_provider().profile(authority.security_profile)
            if (
                not isinstance(profile, (BearerSecurityProfile, ApiKeySecurityProfile))
                or static_principal(profile) != authority.principal
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise _denied("authority_profile_changed") from None

    def authorize_request(
        self, authority: ExecutionAuthority | None, action: str, resource: str
    ) -> None:
        if (
            type(action) is not str
            or not action
            or len(action.encode()) > 128
            or type(resource) is not str
            or not resource
            or len(resource.encode()) > 4096
            or any(ord(item) < 32 for item in action + resource)
        ):
            raise _denied("action_resource_invalid")
        self.validate_authority(authority)
        config = self.config_provider()
        if config.enabled and (
            authority is None
            or not authorize(authority.principal, config.policy, action=action, resource=resource)
        ):
            raise _denied("action_not_permitted")

    def _candidate(
        self, key: str, *, connection: StorageConnection | None = None
    ) -> dict[str, Any] | None:
        connection = connection or self.connection
        value = _record(connection, "execution_authority_candidates", "candidate_key", key)
        if value is not None:
            if (
                set(value) != {"profile", "binding", "authority", "policy", "policy_digest"}
                or value["profile"] != PROFILE
                or value["policy_digest"] != _digest(value["policy"])
                or value["policy"] != self._policy()
            ):
                raise _invalid("authority_candidate_changed")
            self.validate_authority(_authority_descriptor(value["authority"]))
        return value

    def _write_candidate(
        self,
        key: str,
        binding: dict[str, Any],
        authority: ExecutionAuthority | None,
        *,
        connection: StorageConnection | None = None,
    ) -> None:
        connection = connection or self.connection
        self.validate_authority(authority)
        if authority is None:
            return
        policy = self._policy()
        value = {
            "profile": PROFILE,
            "binding": binding,
            "authority": authority.as_value(),
            "policy": policy,
            "policy_digest": _digest(policy),
        }
        original = self._candidate(key, connection=connection)
        if original is not None and original != value:
            raise _invalid("authority_candidate_changed")
        connection.execute(
            "INSERT INTO "
            "execution_authority_candidates(candidate_key,payload,digest) VALUES "
            "(?,?,?) ON CONFLICT(candidate_key) DO NOTHING",
            (key, _encoded(value), _digest(value)),
        )
        if self._candidate(key, connection=connection) != value:
            raise _invalid("authority_candidate_changed")

    def reserve_invocation(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority | None,
        *,
        input_data: Any,
        commit: bool = True,
    ) -> None:
        if authority is None:
            self.validate_authority(None)
            return
        if handle.owner_principal != authority.principal.identity:
            raise _denied("authority_owner_mismatch")
        if self.archive.has_binding(handle):
            raise _invalid("authority_candidate_after_admission")
        with (
            owned_write_transaction(
                self.connection,
                None,
                invocation_id=handle.invocation_id,
                workflow_fingerprint=handle.workflow_fingerprint,
            )
            if commit
            else nullcontext()
        ):
            current = self.invocations.get(handle.invocation_id)
            if (
                current is None
                or _identity(current) != _identity(handle)
                or current.status != "running"
                or self.connection.execute(
                    "SELECT 1 FROM workflow_effect_invocations WHERE invocation_id=? LIMIT 1",
                    (handle.invocation_id,),
                ).fetchone()
                is not None
            ):
                raise _invalid("authority_candidate_after_execution")
            self._write_candidate(
                "invocation:" + handle.invocation_id,
                {
                    "kind": "invocation",
                    "identity": _identity(handle),
                    "input_digest": _digest(input_data),
                },
                authority,
            )

    def reserved_invocation(self, handle: ExecutionHandle) -> ExecutionAuthority | None:
        value = self._candidate("invocation:" + handle.invocation_id)
        if value is None:
            if handle.parent_invocation_id is not None:
                parent = self.invocations.get(handle.parent_invocation_id)
                binding = self.connection.execute(
                    "SELECT 1 FROM child_workflow_bindings WHERE child_id=? AND parent_id=?",
                    (handle.invocation_id, handle.parent_invocation_id),
                ).fetchone()
                if parent is None or binding is None:
                    raise _invalid("authority_parent_changed")
                if _read_authority(self.connection, self.invocations, parent) is not None:
                    raise _invalid("authority_child_candidate_missing")
            return None
        binding = value["binding"]
        _verify_origin(self.connection, self.invocations, handle, value)
        if binding.get("identity") != _identity(handle) or binding.get("kind") not in {
            "invocation",
            "child",
            "scheduled",
        }:
            raise _invalid("authority_candidate_changed")
        if (
            binding["kind"] != "invocation"
            or self.connection.execute(
                "SELECT 1 FROM invocation_inputs WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()
            is not None
        ):
            original = _bounded(
                self.connection,
                "invocation_inputs",
                "invocation_id",
                handle.invocation_id,
                limit=MAX_ARCHIVE_BYTES,
            )
            input_row = self.connection.execute(
                "SELECT fingerprint FROM invocation_inputs WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()
            if (
                input_row is None
                or input_row[0] != handle.workflow_fingerprint
                or _digest(original) != binding["input_digest"]
            ):
                raise _invalid("authority_candidate_input_changed")
        authority = _authority_descriptor(value["authority"])
        if handle.owner_principal != authority.principal.identity:
            raise _invalid("authority_owner_changed")
        if binding["kind"] == "child":
            parent = self.invocations.get(binding["parent_invocation_id"])
            if parent is None or handle.parent_invocation_id != parent.invocation_id:
                raise _invalid("authority_parent_changed")
            parent_record = _read_authority(self.connection, self.invocations, parent)
            row = self.connection.execute(
                "SELECT digest FROM child_workflow_bindings WHERE child_id=? AND parent_id=?",
                (handle.invocation_id, parent.invocation_id),
            ).fetchall()
            if (
                parent_record is None
                or _digest(parent_record) != binding["parent_authority_digest"]
                or len(row) != 1
                or row[0][0] != binding["child_binding_digest"]
            ):
                raise _invalid("authority_parent_changed")
        elif binding["kind"] == "scheduled":
            schedule = self._candidate("schedule:" + binding["schedule_id"])
            slot = self.connection.execute(
                "SELECT invocation_id FROM schedule_slots WHERE slot_key=?", (binding["slot_key"],)
            ).fetchone()
            saved = _bounded(
                self.connection,
                "schedule_bindings",
                "slot_key",
                binding["slot_key"],
                limit=MAX_PROFILE_BYTES,
            )
            definition = _bounded(
                self.connection,
                "schedule_definitions",
                "schedule_id",
                binding["schedule_id"],
                limit=MAX_ARCHIVE_BYTES,
            )
            if (
                schedule is None
                or _digest(schedule) != binding["schedule_authority_digest"]
                or slot is None
                or slot[0] != handle.invocation_id
                or saved is None
                or _digest(saved) != binding["slot_binding_digest"]
                or definition is None
                or _digest(definition) != schedule["binding"]["definition_digest"]
            ):
                raise _invalid("authority_schedule_changed")
        return authority

    def reserve_child(
        self,
        child: ExecutionHandle,
        parent: ExecutionHandle,
        *,
        execution_lease: ExecutionLease | None,
    ) -> None:
        """No-commit participant after the real parent-fenced reservation rows."""
        authority = self.verify_invocation(parent)
        if authority is None:
            return
        if (
            execution_lease is None
            or execution_lease.invocation_id != parent.invocation_id
            or execution_lease.workflow_fingerprint != parent.workflow_fingerprint
        ):
            raise InvocationStateConflict("child authority requires current parent ownership")
        guard, parameters = execution_write_guard(self.connection, execution_lease)
        if self.connection.execute(guard, parameters).fetchone() is None:
            raise InvocationStateConflict("child authority parent ownership changed")
        parent_record = _read_authority(self.connection, self.invocations, parent)
        rows = self.connection.execute(
            "SELECT digest FROM child_workflow_bindings WHERE child_id=? AND parent_id=?",
            (child.invocation_id, parent.invocation_id),
        ).fetchall()
        original = _bounded(
            self.connection,
            "invocation_inputs",
            "invocation_id",
            child.invocation_id,
            limit=MAX_ARCHIVE_BYTES,
        )
        input_row = self.connection.execute(
            "SELECT fingerprint FROM invocation_inputs WHERE invocation_id=?",
            (child.invocation_id,),
        ).fetchone()
        if (
            parent_record is None
            or len(rows) != 1
            or child.parent_invocation_id != parent.invocation_id
            or child.owner_principal != authority.principal.identity
            or input_row is None
            or input_row[0] != child.workflow_fingerprint
        ):
            raise _invalid("authority_parent_changed")
        binding = {
            "kind": "child",
            "identity": _identity(child),
            "input_digest": _digest(original),
            "parent_invocation_id": parent.invocation_id,
            "parent_authority_digest": _digest(parent_record),
            "child_binding_digest": rows[0][0],
        }
        self._write_candidate("invocation:" + child.invocation_id, binding, authority)

    def _schedule_binding(
        self, record: ScheduleRecord, *, connection: StorageConnection | None = None
    ) -> dict[str, Any]:
        connection = connection or self.connection
        definition = _bounded(
            connection,
            "schedule_definitions",
            "schedule_id",
            record.schedule_id,
            limit=MAX_ARCHIVE_BYTES,
        )
        if type(definition) is not dict or any(
            definition.get(name) != getattr(record, name)
            for name in (
                "schedule_id",
                "workflow_name",
                "workflow_version",
                "workflow_fingerprint",
                "workflow_source_json",
                "kind",
                "interval_seconds",
                "owner_principal",
                "event_scope",
                "timezone",
            )
        ):
            raise _invalid("authority_schedule_changed")
        return {
            "kind": "schedule",
            "schedule_id": record.schedule_id,
            "owner_principal": record.owner_principal,
            "definition_digest": _digest(definition),
        }

    def reserve_schedule(
        self,
        record: ScheduleRecord,
        authority: ExecutionAuthority | None = None,
        *,
        commit: bool = True,
        connection: StorageConnection | None = None,
    ) -> None:
        connection = connection or self.connection
        selected = authority or current_authority()
        self.validate_authority(selected)
        if selected is None:
            return
        if record.owner_principal != selected.principal.identity:
            raise _denied("authority_owner_mismatch")
        definition = _bounded(
            connection,
            "schedule_definitions",
            "schedule_id",
            record.schedule_id,
            limit=MAX_ARCHIVE_BYTES,
        )
        if type(definition) is not dict or definition.get("execution_authority_digest") != _digest(
            selected.as_value()
        ):
            raise _invalid("authority_schedule_changed")
        with (
            owned_write_transaction(
                connection,
                None,
                invocation_id=record.schedule_id,
                workflow_fingerprint=record.workflow_fingerprint,
            )
            if commit
            else nullcontext()
        ):
            self._write_candidate(
                "schedule:" + record.schedule_id,
                self._schedule_binding(record, connection=connection),
                selected,
                connection=connection,
            )

    def reserved_schedule(
        self, record: ScheduleRecord, *, connection: StorageConnection | None = None
    ) -> ExecutionAuthority | None:
        connection = connection or self.connection
        candidate = self._candidate("schedule:" + record.schedule_id, connection=connection)
        if candidate is None:
            definition = _bounded(
                connection,
                "schedule_definitions",
                "schedule_id",
                record.schedule_id,
                limit=MAX_ARCHIVE_BYTES,
            )
            if (
                isinstance(definition, dict)
                and definition.get("execution_authority_digest") is not None
            ):
                raise _invalid("authority_schedule_missing")
            self.validate_authority(None)
            return None
        if candidate["binding"] != self._schedule_binding(record, connection=connection):
            raise _invalid("authority_schedule_changed")
        selected = _authority_descriptor(candidate["authority"])
        definition = _bounded(
            connection,
            "schedule_definitions",
            "schedule_id",
            record.schedule_id,
            limit=MAX_ARCHIVE_BYTES,
        )
        if (
            record.owner_principal != selected.principal.identity
            or type(definition) is not dict
            or definition.get("execution_authority_digest") != _digest(selected.as_value())
        ):
            raise _invalid("authority_owner_changed")
        return selected

    def reserve_scheduled_invocation(
        self,
        handle: ExecutionHandle,
        record: ScheduleRecord,
        claim: ScheduleClaim,
        *,
        connection: StorageConnection | None = None,
    ) -> None:
        """No-commit participant after verified current slot/invocation binding."""
        connection = connection or self.connection
        authority = self.reserved_schedule(record, connection=connection)
        if authority is None:
            return
        clock = (
            "EXTRACT(EPOCH FROM clock_timestamp())"
            if connection.is_postgresql
            else "((julianday('now')-2440587.5)*86400.0)"
        )
        row = connection.execute(
            "SELECT slot_key,input_payload FROM schedule_slots WHERE schedule_id=? "
            "AND occurrence_key=? AND owner=? AND generation=? AND invocation_id=? "
            "AND lease_until>" + clock,
            (
                claim.schedule_id,
                claim.occurrence_key,
                claim.owner,
                claim.generation,
                handle.invocation_id,
            ),
        ).fetchone()
        if (
            row is None
            or record.schedule_id != claim.schedule_id
            or handle.owner_principal != authority.principal.identity
        ):
            raise InvocationStateConflict("scheduled authority slot ownership changed")
        slot = _bounded(
            connection, "schedule_bindings", "slot_key", row[0], limit=MAX_PROFILE_BYTES
        )
        candidate = self._candidate("schedule:" + record.schedule_id, connection=connection)
        if slot is None or candidate is None:
            raise _invalid("authority_schedule_changed")
        binding = {
            "kind": "scheduled",
            "identity": _identity(handle),
            "input_digest": _digest(_parsed(row[1], limit=MAX_ARCHIVE_BYTES)),
            "schedule_id": record.schedule_id,
            "slot_key": row[0],
            "schedule_authority_digest": _digest(candidate),
            "slot_binding_digest": _digest(slot),
        }
        self._write_candidate(
            "invocation:" + handle.invocation_id, binding, authority, connection=connection
        )

    def _unbound_legacy(self, identity: EffectIdentity) -> bool:
        """Standalone default-off common services never issue admission authority.

        Remaining original markers, independent proofs or candidates still make
        missing authority a fault; actual engines retain their archive preflight.
        """
        return (
            not self.config_provider().enabled
            and current_authority() is None
            and not _authority_hint(self.connection, identity)
            and self._candidate("invocation:" + identity.invocation_id) is None
        )

    def verify_invocation(self, handle: ExecutionHandle) -> ExecutionAuthority | None:
        value = _read_authority(self.connection, self.invocations, handle)
        if value is not None:
            if value["policy"] != self._policy():
                raise _denied("execution_policy_changed")
            authority = _authority_descriptor(value["authority"])
            self.validate_authority(authority)
            rows = self.connection.execute(
                "SELECT decision_key FROM execution_authorization_decisions WHERE "
                "invocation_id=? ORDER BY decision_key LIMIT ?",
                (handle.invocation_id, MAX_DECISIONS + 1),
            ).fetchall()
            if len(rows) > MAX_DECISIONS:
                raise _invalid("authority_limit")
            for row in rows:
                _read_decision(self.connection, self.invocations, row[0], authority_record=value)
            if value["policy"]["enabled"]:
                intents = self.connection.execute(
                    "SELECT business_key FROM execution_authorization_intents WHERE "
                    "invocation_id=? ORDER BY business_key LIMIT ?",
                    (handle.invocation_id, MAX_DECISIONS + 1),
                ).fetchall()
                if len(intents) > MAX_DECISIONS:
                    raise _invalid("authority_limit")
                for intent_row in intents:
                    intent = _record(
                        self.connection,
                        "execution_authorization_intents",
                        "business_key",
                        intent_row[0],
                    )
                    if intent is None:
                        raise _invalid("authorization_intent_missing_or_changed")
                    business = EffectIdentity.from_value(intent["business_identity"])
                    known = _known_receipt(
                        self.connection,
                        business,
                        limit=value["policy"]["max_business_receipt_bytes"],
                    )
                    _read_receipt(
                        self.connection,
                        business,
                        known,
                        limit=value["policy"]["max_business_receipt_bytes"],
                    )
                    _read_intent(
                        self.connection, self.invocations, business, known, authority_record=value
                    )
                outcomes = self.connection.execute(
                    "SELECT business_key FROM execution_authorization_outcomes WHERE "
                    "invocation_id=? ORDER BY business_key LIMIT ?",
                    (handle.invocation_id, MAX_DECISIONS + 1),
                ).fetchall()
                if len(outcomes) > MAX_DECISIONS:
                    raise _invalid("authority_limit")
                for outcome_row in outcomes:
                    outcome = _record(
                        self.connection,
                        "execution_authorization_outcomes",
                        "business_key",
                        outcome_row[0],
                    )
                    if outcome is None:
                        raise _invalid("authorization_outcome_seal_missing_or_changed")
                    business = EffectIdentity.from_value(outcome["business_identity"])
                    verify_execution_authorization_business_receipt(
                        self.connection,
                        self.invocations,
                        business,
                        _known_receipt(
                            self.connection,
                            business,
                            limit=value["policy"]["max_business_receipt_bytes"],
                        ),
                        authority_record=value,
                    )
                length = (
                    "octet_length(i.payload)"
                    if self.connection.is_postgresql
                    else "length(CAST(i.payload AS BLOB))"
                )
                business_rows = self.connection.execute(
                    f"SELECT CASE WHEN {length}<=? THEN i.payload ELSE NULL END "
                    "FROM workflow_effect_invocations i JOIN "
                    "workflow_effects e ON e.identity=i.identity WHERE i.invocation_id=? "
                    "LIMIT ?",
                    (MAX_RECORD_BYTES, handle.invocation_id, 4097),
                ).fetchall()
                if len(business_rows) > 4096:
                    raise _invalid("authority_limit")
                for business_row in business_rows:
                    identity = EffectIdentity.from_value(_parsed(business_row[0]))
                    if requires_authorization_seal(identity):
                        verify_execution_authorization_business_receipt(
                            self.connection,
                            self.invocations,
                            identity,
                            _known_receipt(
                                self.connection,
                                identity,
                                limit=value["policy"]["max_business_receipt_bytes"],
                            ),
                            authority_record=value,
                        )
                    elif identity.operation.startswith(INTENT_OPERATION + ":"):
                        verify_execution_authorization_intent_receipt(
                            self.connection,
                            self.invocations,
                            identity,
                            _known_receipt(self.connection, identity),
                        )
                    elif identity.operation.startswith(DECISION_OPERATION + ":"):
                        _read_decision(
                            self.connection, self.invocations, identity.key, authority_record=value
                        )
                    elif identity.operation == SEAL_OPERATION:
                        verify_execution_authorization_seal_receipt(
                            self.connection,
                            self.invocations,
                            identity,
                            _known_receipt(self.connection, identity),
                        )
            return authority
        candidate = self.reserved_invocation(handle)
        if candidate is None and self._unbound_legacy(authority_identity(handle)):
            return None
        if self.archive.has_binding(handle):
            if candidate is not None:
                raise _invalid("authority_binding_missing")
            self.validate_authority(None)
            return None
        if (
            self.connection.execute(
                "SELECT 1 FROM workflow_effect_invocations WHERE invocation_id=? LIMIT 1",
                (handle.invocation_id,),
            ).fetchone()
            is not None
        ):
            raise _invalid("authority_original_definition_missing")
        selected = candidate or current_authority()
        self.validate_authority(selected)
        if selected is not None and handle.owner_principal != selected.principal.identity:
            raise _denied("authority_owner_mismatch")
        return selected

    def admission_participant(
        self,
        plan: WorkflowPlan,
        handle: ExecutionHandle,
        input_data: Any,
        execution_lease: ExecutionLease | None,
        first_admission: bool = False,
    ) -> None:
        if execution_lease is None:
            if (
                current_authority() is not None
                or self._candidate("invocation:" + handle.invocation_id) is not None
            ):
                raise InvocationStateConflict("authority admission requires execution ownership")
            self.validate_authority(None)
            return
        existing = _record(
            self.connection, "execution_authorities", "invocation_id", handle.invocation_id
        )
        if existing is not None or not first_admission:
            existing = _read_authority(self.connection, self.invocations, handle)
        candidate = self._candidate("invocation:" + handle.invocation_id)
        selected = (
            _authority_descriptor(existing["authority"])
            if existing
            else self.reserved_invocation(handle) or current_authority()
        )
        self.validate_authority(selected)
        if selected is None:
            return
        if handle.owner_principal != selected.principal.identity:
            raise _denied("authority_owner_mismatch")
        original = _archive_evidence(self.connection, self.invocations, handle)
        if original["authority_digest"] != _digest(selected.as_value()):
            raise _invalid("authority_original_binding_changed")
        if (
            original["input_digest"] != _digest(input_data)
            or plan.fingerprint != handle.workflow_fingerprint
        ):
            raise _invalid("authority_original_binding_changed")
        if (
            candidate is not None
            and candidate["binding"]["input_digest"] != original["input_digest"]
        ):
            raise _invalid("authority_candidate_input_changed")
        policy = self._policy()
        value = {
            "profile": PROFILE,
            "original": original,
            "authority": selected.as_value(),
            "policy": policy,
            "policy_digest": _digest(policy),
            "candidate_digest": _digest(candidate) if candidate else None,
        }
        if existing is not None and existing != value:
            raise _invalid("authority_binding_changed")
        self.connection.execute(
            "INSERT INTO "
            "execution_authorities(invocation_id,fingerprint,payload,digest) "
            "VALUES (?,?,?,?) ON CONFLICT(invocation_id) DO NOTHING",
            (handle.invocation_id, handle.workflow_fingerprint, _encoded(value), _digest(value)),
        )
        insert_completed_receipt(
            self.connection, _authority_identity(handle), _authority_receipt(value)
        )
        _read_authority(self.connection, self.invocations, handle)
        guard, parameters = execution_write_guard(self.connection, execution_lease)
        if self.connection.execute(guard, parameters).fetchone() is None:
            raise InvocationStateConflict("authority admission ownership expired")

    def authorize(
        self,
        handle: ExecutionHandle,
        identity: EffectIdentity,
        action: str,
        resource: str,
        request_digest: str,
        *,
        execution_lease: ExecutionLease | None,
    ) -> AuthorizationDecision | None:
        authority = self.verify_invocation(handle)
        self.authorize_request(authority, action, resource)
        if authority is None:
            return None
        record = _read_authority(self.connection, self.invocations, handle)
        if record is None:
            raise _invalid("authority_binding_missing")
        if not record["policy"]["enabled"]:
            return None
        if (
            identity.invocation_id != handle.invocation_id
            or identity.workflow_fingerprint != handle.workflow_fingerprint
            or execution_lease is None
        ):
            raise InvocationStateConflict(
                "action authorization requires original execution ownership"
            )
        if (
            type(request_digest) is not str
            or len(request_digest) != 64
            or any(item not in "0123456789abcdef" for item in request_digest)
        ):
            raise _denied("request_digest_invalid")
        policy = self._policy()
        audit: dict[str, Any] = {
            "principal": authority.principal.identity,
            "action": action,
            "resource": resource,
            "decision": "allow",
            "policy_id": policy["id"],
            "policy_version": policy["version"],
            "policy_digest": _digest(policy),
            "policy_enforced": policy["enabled"],
            "generation": execution_lease.generation,
        }
        value: dict[str, Any] = {
            "profile": PROFILE,
            "business_identity": _effect(identity),
            "authority_digest": _digest(record),
            "request_digest": request_digest,
            "audit": audit,
        }
        selected = decision_identity(identity, action, resource)
        with owned_write_transaction(
            self.connection,
            execution_lease,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=handle.workflow_fingerprint,
        ):
            original = _record(
                self.connection, "execution_authorization_decisions", "decision_key", selected.key
            )
            if original is not None:
                # Historical generation is immutable; reuse requires identical
                # logical request/authority/policy and a current execution fence.
                value["audit"]["generation"] = original["audit"]["generation"]
                if original != value:
                    raise _invalid("authorization_decision_changed")
            else:
                count = self.connection.execute(
                    "SELECT COUNT(*) FROM execution_authorization_decisions WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchone()[0]
                if count >= MAX_DECISIONS:
                    raise _invalid("authority_limit")
                self.connection.execute(
                    "INSERT INTO "
                    "execution_authorization_decisions(decision_key,business_key,invocation"
                    "_id,fingerprint,payload,digest) VALUES (?,?,?,?,?,?)",
                    (
                        selected.key,
                        identity.key,
                        handle.invocation_id,
                        handle.workflow_fingerprint,
                        _encoded(value),
                        _digest(value),
                    ),
                )
            receipt = _decision_receipt(value)
            insert_completed_receipt(self.connection, selected, receipt)
            _read_decision(self.connection, self.invocations, selected.key, authority_record=record)
            guard, parameters = execution_write_guard(self.connection, execution_lease)
            if self.connection.execute(guard, parameters).fetchone() is None:
                raise InvocationStateConflict("action authorization ownership expired")
        return AuthorizationDecision(selected, receipt, dict(value["audit"]))

    def inspect_decision(
        self, identity: EffectIdentity, action: str, resource: str, request_digest: str
    ) -> AuthorizationDecision:
        """Verify an existing exact request binding without creating a decision."""
        selected = decision_identity(identity, action, resource)
        value = _read_decision(self.connection, self.invocations, selected.key)
        if (
            value is None
            or value["business_identity"] != _effect(identity)
            or value["request_digest"] != request_digest
            or value["audit"]["action"] != action
            or value["audit"]["resource"] != resource
        ):
            raise _invalid("authorization_decision_changed")
        return AuthorizationDecision(selected, _decision_receipt(value), dict(value["audit"]))

    def seal_intent(
        self,
        identity: EffectIdentity,
        receipt: EffectReceipt,
        *,
        execution_lease: ExecutionLease | None,
        first_intent: bool = False,
    ) -> AuthorizationDecision | None:
        if not requires_authorization_seal(identity):
            return None
        handle = self.invocations.get(identity.invocation_id)
        if handle is None:
            if self._unbound_legacy(identity):
                return None
            raise _invalid("authorization_invocation_missing")
        authority = _read_authority(self.connection, self.invocations, handle)
        if authority is None:
            if _authority_hint(self.connection, identity):
                raise _invalid("authority_binding_missing")
            return None
        if not authority["policy"]["enabled"]:
            return None
        if authority["policy"] != self._policy():
            raise _denied("execution_policy_changed")
        self.validate_authority(_authority_descriptor(authority["authority"]))
        if (
            execution_lease is None
            or execution_lease.invocation_id != identity.invocation_id
            or execution_lease.workflow_fingerprint != identity.workflow_fingerprint
        ):
            raise InvocationStateConflict("intent audit requires current execution ownership")
        guard, parameters = execution_write_guard(self.connection, execution_lease)
        if self.connection.execute(guard, parameters).fetchone() is None:
            raise InvocationStateConflict("intent audit ownership changed")
        existing = _record(
            self.connection, "execution_authorization_intents", "business_key", identity.key
        )
        if existing is not None and first_intent:
            raise _invalid("authorization_intent_business_missing")
        if existing is not None or not first_intent:
            existing = _read_intent(
                self.connection, self.invocations, identity, receipt, authority_record=authority
            )
            return AuthorizationDecision(
                intent_identity(identity),
                _intent_receipt(existing),
                {"generation": existing["generation"]},
            )
        if (
            receipt.status != "pending"
            or receipt.output is not None
            or receipt.error is not None
            or receipt.child_invocation is not None
            or receipt.external_outcome is not None
        ):
            raise _invalid("authorization_intent_changed")
        decisions = self.connection.execute(
            "SELECT decision_key FROM execution_authorization_decisions WHERE "
            "business_key=? LIMIT ?",
            (identity.key, MAX_BUSINESS_DECISIONS + 1),
        ).fetchall()
        if not 1 <= len(decisions) <= MAX_BUSINESS_DECISIONS:
            raise _invalid("authorization_decision_missing")
        for decision in decisions:
            _read_decision(
                self.connection, self.invocations, decision[0], authority_record=authority
            )
        count = self.connection.execute(
            "SELECT COUNT(*) FROM execution_authorization_intents WHERE invocation_id=?",
            (identity.invocation_id,),
        ).fetchone()[0]
        if count >= MAX_DECISIONS:
            raise _invalid("authority_limit")
        value = {
            "profile": PROFILE,
            "business_identity": _effect(identity),
            "authority_digest": _digest(authority),
            "pending_receipt": _receipt_payload(receipt),
            "generation": execution_lease.generation,
        }
        self.connection.execute(
            "INSERT INTO "
            "execution_authorization_intents(business_key,invocation_id,fingerprint"
            ",payload,digest) VALUES (?,?,?,?,?)",
            (
                identity.key,
                identity.invocation_id,
                identity.workflow_fingerprint,
                _encoded(value),
                _digest(value),
            ),
        )
        insert_completed_receipt(self.connection, intent_identity(identity), _intent_receipt(value))
        if self.connection.execute(guard, parameters).fetchone() is None:
            raise InvocationStateConflict("intent audit ownership expired")
        return AuthorizationDecision(
            intent_identity(identity),
            _intent_receipt(value),
            {"generation": execution_lease.generation},
        )

    def seal_business(
        self,
        identity: EffectIdentity,
        receipt: EffectReceipt,
        *,
        execution_lease: ExecutionLease | None,
    ) -> None:
        if not requires_authorization_seal(identity) or receipt.status == "pending":
            return
        handle = self.invocations.get(identity.invocation_id)
        if handle is None:
            if self._unbound_legacy(identity):
                return None
            raise _invalid("authorization_invocation_missing")
        authority = self.verify_invocation(handle)
        if authority is None:
            if _authority_hint(self.connection, identity):
                raise _invalid("authority_binding_missing")
            return
        record = _read_authority(self.connection, self.invocations, handle)
        if record is None:
            raise _invalid("authority_binding_missing")
        if not record["policy"]["enabled"]:
            return
        if execution_lease is None:
            raise InvocationStateConflict(
                "authorization outcome requires current execution ownership"
            )
        guard, parameters = execution_write_guard(self.connection, execution_lease)
        if (
            execution_lease.invocation_id != identity.invocation_id
            or execution_lease.workflow_fingerprint != identity.workflow_fingerprint
            or self.connection.execute(guard, parameters).fetchone() is None
        ):
            raise InvocationStateConflict("authorization outcome ownership changed")
        try:
            _encoded(
                _receipt_payload(receipt), limit=record["policy"]["max_business_receipt_bytes"]
            )
        except (ValueError, TypeError, OwaError):
            raise AuthorizationOutcomeUnavailable(
                "business outcome exceeds its protected audit boundary"
            ) from None
        keys = [
            row[0]
            for row in self.connection.execute(
                "SELECT decision_key FROM execution_authorization_decisions WHERE "
                "business_key=? ORDER BY decision_key LIMIT ?",
                (identity.key, MAX_BUSINESS_DECISIONS + 1),
            ).fetchall()
        ]
        if not 1 <= len(keys) <= MAX_BUSINESS_DECISIONS:
            raise _invalid("authorization_decision_missing")
        dependencies = []
        for key in keys:
            decision = _read_decision(
                self.connection, self.invocations, key, authority_record=record
            )
            selected = decision_identity(
                identity, decision["audit"]["action"], decision["audit"]["resource"]
            )
            dependencies.append(
                {
                    "key": key,
                    "receipt_digest": ReceiptDependency.capture(
                        selected, _decision_receipt(decision)
                    ).receipt_digest,
                }
            )
        value = {
            "profile": PROFILE,
            "business_identity": _effect(identity),
            "authority_digest": _digest(record),
            "receipt_digest": ReceiptDependency.capture(identity, receipt).receipt_digest,
            "decisions": dependencies,
            "intent_digest": _digest(intent)
            if (
                intent := _record(
                    self.connection, "execution_authorization_intents", "business_key", identity.key
                )
            )
            is not None
            else None,
        }
        original = _record(
            self.connection, "execution_authorization_outcomes", "business_key", identity.key
        )
        if original is not None and original != value:
            raise _invalid("authorization_outcome_seal_changed")
        self.connection.execute(
            "INSERT INTO "
            "execution_authorization_outcomes(business_key,invocation_id,fingerprin"
            "t,payload,digest) VALUES (?,?,?,?,?) ON CONFLICT(business_key) DO "
            "NOTHING",
            (
                identity.key,
                identity.invocation_id,
                identity.workflow_fingerprint,
                _encoded(value),
                _digest(value),
            ),
        )
        insert_completed_receipt(
            self.connection, replace(identity, operation=SEAL_OPERATION), _seal_receipt(value)
        )
        if self.connection.execute(guard, parameters).fetchone() is None:
            raise InvocationStateConflict("authorization outcome ownership expired")
