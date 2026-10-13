"""Leased immutable executable-source decisions; never fetch or execute source."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectReceipt, ReceiptDependency, effect_request_digest
from .errors import InvocationStateConflict, OwaError, WorkflowExecutionError
from .ownership import ExecutionLease, execution_write_guard, owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore
from .recovery_definitions import (
    ADMISSION_OPERATION,
    MAX_PROFILE_BYTES,
    _stored_evidence,
    admission_identity,
    verify_definition_admission_receipt,
)
from .storage import StorageConnection

if TYPE_CHECKING:
    from .executable_sources import ExecutableSource, ExecutableSourceRegistry
    from .services import RuntimeServices
    from .workflow import WorkflowPlan

SOURCE_POLICY = "policy:executable-source:v1"
_PROFILE = "owa/executable-source-admission/v1"
_MAX_RECORD_BYTES = 262_144
_MAX_BUSINESS_RECEIPT_BYTES = 64_000_000
_OWNER = "executable-source-admission"


def _failure() -> WorkflowExecutionError:
    return WorkflowExecutionError("executable source admission evidence is inconsistent")


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate source admission field")
        result[name] = value
    return result


def _parsed(raw: str, *, limit: int = _MAX_RECORD_BYTES) -> Any:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise _failure()
    value = json.loads(raw, object_pairs_hook=_unique)
    effect_request_digest(_PROFILE, value)
    return value


def _encoded(value: Any) -> str:
    effect_request_digest(_PROFILE, value)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(raw.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise _failure()
    return raw


def _identity(identity: EffectIdentity) -> dict[str, Any]:
    # This is a known internal dataclass boundary, not user-input coercion.
    return {
        "invocation_id": identity.invocation_id,
        "workflow_fingerprint": identity.workflow_fingerprint,
        "task_reference": identity.task_reference,
        "occurrence": [[reference, visit] for reference, visit in identity.occurrence],
        "operation": identity.operation,
    }


def _source_binding(source: ExecutableSource) -> dict[str, Any]:
    source.verified_bytes()
    return {
        "profile": "owa/executable-source/v1",
        "id": source.id,
        "version": source.version,
        "uri": source.uri,
        "language": source.language,
        "digest": source.digest,
    }


def _invocation_binding(handle: ExecutionHandle) -> dict[str, Any]:
    return {
        name: getattr(handle, name)
        for name in (
            "invocation_id",
            "engine",
            "workflow_name",
            "workflow_version",
            "workflow_fingerprint",
            "session_id",
            "user_id",
            "owner_principal",
            "parent_invocation_id",
            "parent_task_reference",
        )
    }


def _receipt(
    connection: StorageConnection, identity: EffectIdentity, *, limit: int | None = None
) -> EffectReceipt | None:
    if limit is None:
        limit = (
            MAX_PROFILE_BYTES + 1024
            if identity.operation == ADMISSION_OPERATION
            else _MAX_RECORD_BYTES
        )
    size = "octet_length(payload)" if connection.is_postgresql else "length(CAST(payload AS BLOB))"
    row = connection.execute(
        f"SELECT owner,status,CASE WHEN {size}<=? THEN payload ELSE NULL END "
        "FROM workflow_effects WHERE identity=?",
        (limit, identity.key),
    ).fetchone()
    index = connection.execute(
        f"SELECT invocation_id,fingerprint,CASE WHEN {size}<=? THEN payload ELSE NULL END "
        "FROM workflow_effect_invocations WHERE identity=?",
        (_MAX_RECORD_BYTES, identity.key),
    ).fetchone()
    if row is None and index is None:
        return None
    try:
        if row is None or index is None or row[2] is None or index[2] is None:
            raise ValueError("orphan source admission evidence")
        receipt = EffectReceipt(**_parsed(row[2], limit=limit))
        if (
            tuple(row[:2]) != (receipt.owner, receipt.status)
            or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
            or EffectIdentity.from_value(_parsed(index[2])) != identity
        ):
            raise ValueError("changed source policy index")
        return receipt
    except (ValueError, TypeError, KeyError):
        raise _failure() from None


def _business_present(connection: StorageConnection, identity: EffectIdentity) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM workflow_effects WHERE identity=? UNION ALL "
            "SELECT 1 FROM workflow_effect_invocations WHERE identity=? LIMIT 1",
            (identity.key, identity.key),
        ).fetchone()
        is not None
    )


def _business_limit(binding: dict[str, Any]) -> int:
    try:
        budget = binding["runtime_profile"]["policy"]["sandbox"]["max_output_bytes"]
        return source_business_receipt_limit(budget)
    except (ValueError, TypeError, KeyError):
        raise _failure() from None


def source_business_receipt_limit(budget: int) -> int:
    """Bound retained source outcomes before admission using the output policy."""
    try:
        if type(budget) is not int or budget <= 0:
            raise ValueError
        # JSON can escape each single UTF8 control byte to six ASCII bytes.
        # stdout+stderr share the configured budget. Error/metadata and the
        # bounded background acceptance payload receive a separate allowance.
        limit = 6 * budget + 1_048_576
        if limit > _MAX_BUSINESS_RECEIPT_BYTES:
            raise ValueError
        return limit
    except (ValueError, TypeError, KeyError):
        raise WorkflowExecutionError(
            "executable source output profile exceeds its finite receipt boundary"
        ) from None


def _captured(plan: WorkflowPlan, identity: EffectIdentity) -> ExecutableSource:
    tasks = [
        task
        for scope in plan.scopes
        for task in scope.tasks
        if (task.execution_reference or task.reference) == identity.task_reference
    ]
    if len(tasks) != 1 or identity.operation != "run:script":
        raise _failure()
    matches = [
        item.source for item in plan.script_resources if item.reference == tasks[0].reference
    ]
    if len(matches) != 1:
        raise _failure()
    matches[0].verified_bytes()
    return matches[0]


def _deadline(value: Any) -> None:
    if value is not None and (
        type(value) not in {int, float} or not math.isfinite(value) or value <= 0
    ):
        raise _failure()


def _verify_record(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> tuple[dict[str, Any], ExecutableSource]:
    try:
        value = receipt.output
        if (
            invocations is None
            or identity.operation != SOURCE_POLICY
            or receipt.owner != _OWNER
            or receipt.status != "completed"
            or receipt.child_invocation is not None
            or receipt.external_outcome is not None
            or not isinstance(value, dict)
            or set(value)
            != {
                "profile",
                "business_identity",
                "request_digest",
                "input_digest",
                "invocation_binding",
                "definition_receipt",
                "source_binding",
                "deadline_at",
            }
            or value["profile"] != _PROFILE
            or not isinstance(value["request_digest"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["request_digest"]) is None
            or receipt.request_digest != effect_request_digest(SOURCE_POLICY, value)
        ):
            raise ValueError("invalid executable policy")
        business = EffectIdentity.from_value(value["business_identity"])
        if (
            business.operation != "run:script"
            or replace(business, operation=SOURCE_POLICY) != identity
        ):
            raise ValueError("changed run identity")
        row = connection.execute(
            "SELECT invocation_id,fingerprint,CASE WHEN length(payload)<=? THEN payload "
            "ELSE NULL END,digest FROM executable_source_admissions WHERE business_key=?",
            (_MAX_RECORD_BYTES, business.key),
        ).fetchone()
        if (
            row is None
            or row[2] is None
            or tuple(row[:2]) != (business.invocation_id, business.workflow_fingerprint)
            or _parsed(row[2]) != value
            or row[3] != receipt.request_digest
        ):
            raise ValueError("changed independent source decision")
        handle = invocations.get(business.invocation_id)
        if handle is None or _invocation_binding(handle) != value["invocation_binding"]:
            raise ValueError("changed original principal or identity")
        plan, binding = _stored_evidence(connection, invocations, handle)
        # A retained business index with a deleted receipt is unresolved. The
        # body uses its original output allowance, not the code-free policy cap.
        if _business_present(connection, business):
            _receipt(connection, business, limit=_business_limit(binding))
        if value["input_digest"] != effect_request_digest(
            "owa/executable-source-input/v1", invocations.read_input(handle)
        ):
            raise ValueError("changed original input")
        dependency = ReceiptDependency.from_value(value["definition_receipt"])
        if dependency.identity != admission_identity(handle):
            raise ValueError("changed definition proof identity")
        definition = _receipt(connection, dependency.identity)
        if (
            definition is None
            or ReceiptDependency.capture(dependency.identity, definition) != dependency
        ):
            raise ValueError("changed original definition admission")
        verify_definition_admission_receipt(
            connection, invocations, dependency.identity, definition
        )
        source = _captured(plan, business)
        if _source_binding(source) != value["source_binding"]:
            raise ValueError("changed captured executable source")
        _deadline(value["deadline_at"])
        return value, source
    except (ValueError, TypeError, KeyError):
        raise _failure() from None


def verify_executable_source_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """Verify original saved evidence with no network, lease or runtime writes."""
    if _receipt(connection, identity) != receipt:
        raise _failure()
    _verify_record(connection, invocations, identity, receipt)


def _table_exists(connection: StorageConnection, name: str) -> bool:
    # Names are private constants below; no bootstrap or arbitrary SQL names.
    if connection.is_postgresql:
        return connection.execute("SELECT to_regclass(?)", (name,)).fetchone()[0] is not None
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def verify_executable_source_business_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """Business-only native replay still requires the original source decision.

    An old generic inline-script journal with no original archive/source profile
    retains its existing contract. Retained profile evidence is never adopted,
    regenerated or treated as optional when a source/binding is missing.
    """
    if identity.operation != "run:script":
        return
    try:
        policy = replace(identity, operation=SOURCE_POLICY)
        retained = _receipt(connection, policy)
        source_table = _table_exists(connection, "executable_source_admissions")
        independent = (
            source_table
            and connection.execute(
                "SELECT 1 FROM executable_source_admissions WHERE business_key=?", (identity.key,)
            ).fetchone()
            is not None
        )
        handle = invocations.get(identity.invocation_id) if invocations is not None else None
        if retained is not None or independent:
            if retained is None:
                raise _failure()
            value, _ = _verify_record(connection, invocations, policy, retained)
            if handle is None or invocations is None:
                raise _failure()
            _, binding = _stored_evidence(connection, invocations, handle)
            if (
                _receipt(connection, identity, limit=_business_limit(binding)) != receipt
                or value["request_digest"] != receipt.request_digest
            ):
                raise _failure()
            return
        # Missing source policy must not permit business-only replay to bypass
        # a previously admitted original definition or an orphan source record.
        archived = (
            (
                _table_exists(connection, "recovery_invocation_definitions")
                and connection.execute(
                    "SELECT 1 FROM recovery_invocation_definitions WHERE invocation_id=?",
                    (identity.invocation_id,),
                ).fetchone()
                is not None
            )
            or (
                _table_exists(connection, "recovery_definitions")
                and connection.execute(
                    "SELECT 1 FROM recovery_definitions WHERE fingerprint=?",
                    (identity.workflow_fingerprint,),
                ).fetchone()
                is not None
            )
            or (handle is not None and _receipt(connection, admission_identity(handle)) is not None)
            or (
                source_table
                and connection.execute(
                    "SELECT 1 FROM executable_source_admissions WHERE invocation_id=? LIMIT 1",
                    (identity.invocation_id,),
                ).fetchone()
                is not None
            )
        )
        if not archived:
            return
        if handle is None or invocations is None:
            raise _failure()
        plan, _ = _stored_evidence(connection, invocations, handle)
        definition = _receipt(connection, admission_identity(handle))
        if definition is None:
            raise _failure()
        verify_definition_admission_receipt(
            connection, invocations, admission_identity(handle), definition
        )
        task = next(
            (
                task
                for scope in plan.scopes
                for task in scope.tasks
                if (task.execution_reference or task.reference) == identity.task_reference
            ),
            None,
        )
        if task is None:
            raise _failure()
        script = task.definition.get("run", {}).get("script", {})
        if (
            any(item.reference == task.reference for item in plan.script_resources)
            or isinstance(script, dict)
            and "source" in script
        ):
            raise _failure()
    except OwaError:
        raise
    except Exception:
        raise _failure() from None


@dataclass(frozen=True, slots=True)
class ExecutableSourceDecision:
    identity: EffectIdentity
    receipt: EffectReceipt
    source: ExecutableSource


class ExecutableSourceAdmission:
    def __init__(self, services: RuntimeServices, registry: ExecutableSourceRegistry):
        self.services, self.registry = services, registry
        self.connection = services.effect_journal.connection
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS executable_source_admissions "
            "(business_key TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,"
            "fingerprint TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.commit()

    def prepare(
        self,
        identity: EffectIdentity,
        payload: Any,
        source: ExecutableSource,
        handle: ExecutionHandle,
        *,
        execution_lease: ExecutionLease | None,
        deadline_at: float | None,
        allow_new: bool = True,
    ) -> ExecutableSourceDecision:
        """Capture only an original archived artifact under its current owner."""
        if (
            execution_lease is None
            or identity.operation != "run:script"
            or identity.invocation_id != handle.invocation_id
            or identity.workflow_fingerprint != handle.workflow_fingerprint
            or execution_lease.engine != handle.engine
            or execution_lease.invocation_id != handle.invocation_id
            or execution_lease.workflow_fingerprint != handle.workflow_fingerprint
        ):
            raise InvocationStateConflict("executable source execution authority is unavailable")
        _deadline(deadline_at)
        request_digest = effect_request_digest("run:script", payload)
        policy = replace(identity, operation=SOURCE_POLICY)
        with owned_write_transaction(
            self.connection,
            execution_lease,
            invocation_id=identity.invocation_id,
            workflow_fingerprint=identity.workflow_fingerprint,
        ):
            current = self.services.invocations.get(handle.invocation_id)
            if (
                current is None
                or current.status != "running"
                or _invocation_binding(current) != _invocation_binding(handle)
            ):
                raise InvocationStateConflict("executable source original parent identity changed")
            plan = self.services.recovery_definitions.verify_admission(handle)
            _, original_binding = _stored_evidence(
                self.connection, self.services.invocations, handle
            )
            # Qualify the source-output profile before committing any decision
            # or executable business intent, including on the initial run.
            _business_limit(original_binding)
            captured = _captured(plan, identity)
            if captured != source or self.registry != self.services.executable_sources:
                raise _failure()
            self.registry.verify(captured)
            prior = _receipt(self.connection, policy)
            if prior is not None:
                value, retained = _verify_record(
                    self.connection, self.services.invocations, policy, prior
                )
                if value["request_digest"] != request_digest or value["deadline_at"] != deadline_at:
                    raise _failure()
                decision = ExecutableSourceDecision(policy, prior, retained)
            else:
                if (
                    not allow_new
                    or _business_present(self.connection, identity)
                    or self.connection.execute(
                        "SELECT 1 FROM executable_source_admissions WHERE business_key=?",
                        (identity.key,),
                    ).fetchone()
                    is not None
                ):
                    raise _failure()
                if deadline_at is not None:
                    clock = (
                        "EXTRACT(EPOCH FROM clock_timestamp())"
                        if self.connection.is_postgresql
                        else "((julianday('now')-2440587.5)*86400.0)"
                    )
                    if self.connection.execute(f"SELECT {clock}").fetchone()[0] >= deadline_at:
                        raise WorkflowExecutionError("executable source original deadline expired")
                definition_identity = admission_identity(handle)
                definition = _receipt(self.connection, definition_identity)
                if definition is None:
                    raise _failure()
                dependency = ReceiptDependency.capture(definition_identity, definition)
                value = {
                    "profile": _PROFILE,
                    "business_identity": _identity(identity),
                    "request_digest": request_digest,
                    "input_digest": effect_request_digest(
                        "owa/executable-source-input/v1",
                        self.services.invocations.read_input(handle),
                    ),
                    "invocation_binding": _invocation_binding(handle),
                    "definition_receipt": {
                        "identity": _identity(dependency.identity),
                        "receipt_digest": dependency.receipt_digest,
                    },
                    "source_binding": _source_binding(captured),
                    "deadline_at": deadline_at,
                }
                receipt = EffectReceipt(
                    effect_request_digest(SOURCE_POLICY, value),
                    _OWNER,
                    status="completed",
                    output=value,
                )
                self.connection.execute(
                    "INSERT INTO executable_source_admissions "
                    "(business_key,invocation_id,fingerprint,payload,digest) VALUES(?,?,?,?,?)",
                    (
                        identity.key,
                        identity.invocation_id,
                        identity.workflow_fingerprint,
                        _encoded(value),
                        receipt.request_digest,
                    ),
                )
                insert_completed_receipt(self.connection, policy, receipt)
                _verify_record(self.connection, self.services.invocations, policy, receipt)
                decision = ExecutableSourceDecision(policy, receipt, captured)
            # Source parsing and independent evidence checks cannot extend a
            # short execution lease. Recheck before the policy transaction commits.
            guard, parameters = execution_write_guard(self.connection, execution_lease)
            if self.connection.execute(guard, parameters).fetchone() is None:
                raise InvocationStateConflict("executable source execution ownership was lost")
        return decision
