"""Private approval policy/audit proofs; writes participate in the common event transaction."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectReceipt, ReceiptDependency, effect_request_digest
from .errors import (
    ApprovalAuthorizationError,
    ApprovalConflict,
    ApprovalValidationError,
    WorkflowExecutionError,
)
from .events import EventEnvelope
from .persistence import InvocationStore
from .protected_approval_policy import (
    PROTECTED_APPROVAL_PROFILE,
    ProtectedApprovalPolicy,
    actor_identity,
    bounded_json,
    original_expiry,
)
from .recovery_definitions import _stored_evidence, admission_identity
from .storage import StorageConnection

if TYPE_CHECKING:
    from .approvals import ApprovalRecord, ApprovalStore

REQUEST_POLICY = "policy:approval-request:v1"
MUTATION_POLICY = "policy:approval-mutation:v1"
REQUEST_PROFILE = "owa/protected-approval-request/v1"
MUTATION_PROFILE = "owa/protected-approval-mutation/v1"
_OWNER = "protected-approvals-v1"
MAX_REASSIGNMENTS = 128
MAX_INVOCATION_ROWS = 10_000
MAX_INVOCATION_INDEX_BYTES = 8_388_608
MAX_INDEX_PAYLOAD_BYTES = 65_536
_HISTORY_LIMITS = {
    "rows": MAX_INVOCATION_ROWS,
    "indexBytes": MAX_INVOCATION_INDEX_BYTES,
    "indexPayloadBytes": MAX_INDEX_PAYLOAD_BYTES,
}


def _tables(connection: StorageConnection) -> tuple[str, str]:
    prefix = "owa_approvals." if connection.is_postgresql else ""
    return prefix + "protected_approval_requests", prefix + "protected_approval_audit"


def table_exists(connection: StorageConnection, table: str) -> bool:
    if connection.is_postgresql:
        return connection.execute("SELECT to_regclass(?)", (table,)).fetchone()[0] is not None
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _failure() -> WorkflowExecutionError:
    return WorkflowExecutionError("protected approval evidence is unavailable or inconsistent")


def _receipt(connection: StorageConnection, identity: EffectIdentity) -> EffectReceipt | None:
    row = connection.execute(
        "SELECT owner,status,CASE WHEN length(payload)<=1048576 THEN payload ELSE NULL END "
        "FROM workflow_effects WHERE identity=?",
        (identity.key,),
    ).fetchone()
    index = connection.execute(
        "SELECT invocation_id,fingerprint,CASE WHEN length(payload)<=1048576 "
        "THEN payload ELSE NULL END FROM workflow_effect_invocations "
        "WHERE identity=?",
        (identity.key,),
    ).fetchone()
    if row is None and index is None:
        return None
    try:
        if row is None or index is None:
            raise ValueError
        receipt = EffectReceipt(**json.loads(row[2]))
        if (
            tuple(row[:2]) != (receipt.owner, receipt.status)
            or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
            or EffectIdentity.from_value(json.loads(index[2])) != identity
        ):
            raise ValueError
        bounded_json(json.loads(row[2]), 1_048_576)
        return receipt
    except (ValueError, TypeError, KeyError):
        raise _failure() from None


def _known(value: dict[str, Any], operation: str) -> EffectReceipt:
    return EffectReceipt(
        effect_request_digest(operation, value), _OWNER, status="completed", output=value
    )


def _identity_value(identity: EffectIdentity) -> dict[str, Any]:
    """Project the private typed identity into finite JSON before hashing."""
    return {
        "invocation_id": identity.invocation_id,
        "workflow_fingerprint": identity.workflow_fingerprint,
        "task_reference": identity.task_reference,
        "occurrence": [list(item) for item in identity.occurrence],
        "operation": identity.operation,
    }


def _dependency_value(dependency: ReceiptDependency) -> dict[str, Any]:
    return {
        "identity": _identity_value(dependency.identity),
        "receipt_digest": dependency.receipt_digest,
    }


def _identity(value: Any) -> EffectIdentity:
    return EffectIdentity.from_value(value)


def _binding(handle: Any) -> dict[str, Any]:
    return {
        name: getattr(handle, name)
        for name in (
            "invocation_id",
            "workflow_fingerprint",
            "engine",
            "session_id",
            "user_id",
            "owner_principal",
            "workflow_name",
            "workflow_version",
            "parent_invocation_id",
            "parent_task_reference",
        )
    }


def _request_value(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    approval_id: str,
    *,
    allow_unfinished: bool = False,
) -> tuple[dict[str, Any], Any]:
    requests, audit = _tables(connection)
    row = connection.execute(
        "SELECT CASE WHEN length(payload)<=524288 THEN payload ELSE NULL END,"
        f"digest,revision,assignee,audit_digest,invocation_id,fingerprint FROM {requests} "
        "WHERE approval_id=?",
        (approval_id,),
    ).fetchone()
    if row is None:
        raise _failure()
    try:
        value = json.loads(row[0])
        bounded_json(value, 524_288)
        if (
            set(value)
            != {
                "profile",
                "approval_id",
                "policy",
                "event_scope",
                "request_event",
                "origin",
                "origin_request_digest",
                "invocation_binding",
                "input_digest",
                "definition_receipt",
                "expires_at",
                "history_limits",
            }
            or value["profile"] != REQUEST_PROFILE
            or value["approval_id"] != approval_id
            or value["history_limits"] != _HISTORY_LIMITS
        ):
            raise ValueError
        origin = _identity(value["origin"])
        if (
            origin.operation != "emit"
            or invocations is None
            or tuple(row[5:]) != (origin.invocation_id, origin.workflow_fingerprint)
        ):
            raise ValueError
        policy = replace(origin, operation=REQUEST_POLICY)
        expected = _known(value, REQUEST_POLICY)
        if row[1] != expected.request_digest or _receipt(connection, policy) != expected:
            raise ValueError
        handle = invocations.get(origin.invocation_id)
        if (
            handle is None
            or _binding(handle) != value["invocation_binding"]
            or handle.owner_principal != value["event_scope"]
        ):
            raise ValueError
        _stored_evidence(connection, invocations, handle)
        if (
            effect_request_digest(REQUEST_PROFILE, invocations.read_input(handle))
            != value["input_digest"]
        ):
            raise ValueError
        dependency = ReceiptDependency.from_value(value["definition_receipt"])
        definition = _receipt(connection, admission_identity(handle))
        if (
            dependency.identity != admission_identity(handle)
            or definition is None
            or ReceiptDependency.capture(dependency.identity, definition) != dependency
        ):
            raise ValueError
        source = value["request_event"]
        selected = ProtectedApprovalPolicy.from_value(value["policy"])
        if (
            source["id"] != approval_id
            or source["subject"] != approval_id
            or source["data"]["approvalPolicy"] != selected.as_value()
            or original_expiry(source["approvalexpiresat"]) != value["expires_at"]
        ):
            raise ValueError
        publication = _receipt(connection, origin)
        if publication is None:
            if not allow_unfinished:
                raise ValueError
        elif (
            publication.status != "completed"
            or publication.output != source
            or publication.request_digest != value["origin_request_digest"]
        ):
            raise ValueError
        records = "owa_approvals.approvals" if connection.is_postgresql else "approvals"
        state = connection.execute(
            "SELECT status,CASE WHEN length(request_event)<=1048576 "
            "THEN request_event ELSE NULL END,"
            "expires_at,CASE WHEN length(decision_event)<=1048576 "
            "THEN decision_event ELSE NULL END,"
            "event_scope,operator_id,decision_operation_key,decided_at "
            f"FROM {records} WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if (
            state is None
            or json.loads(state[1]) != source
            or state[2] != value["expires_at"]
            or state[4] != value["event_scope"]
        ):
            raise ValueError
        revision, assignee, digest = 1, selected.assignee, expected.request_digest
        terminal = None
        terminal_projection = (None, None, None)
        entries = connection.execute(
            "SELECT revision,CASE WHEN length(payload)<=524288 THEN payload ELSE NULL END,"
            f"digest,operation_key FROM {audit} WHERE approval_id=? "
            "ORDER BY revision LIMIT 131",
            (approval_id,),
        ).fetchall()
        if len(entries) > MAX_REASSIGNMENTS:
            raise ValueError
        for entry in entries:
            item = json.loads(entry[1])
            if (
                terminal is not None
                or set(item)
                != {
                    "profile",
                    "approval_id",
                    "revision",
                    "previous_digest",
                    "operation_key",
                    "request",
                    "recorded_at",
                    "event",
                }
                or not isinstance(item["request"], dict)
                or set(item["request"]) != {"action", "actor", "revision", "assignee", "value"}
                or entry[0] != revision + 1
                or item["revision"] != revision + 1
                or item["previous_digest"] != digest
                or item["approval_id"] != approval_id
                or item["profile"] != MUTATION_PROFILE
                or item["operation_key"] != entry[3]
            ):
                raise ValueError
            bounded_json(item, 524_288)
            proof = _known(item, MUTATION_POLICY)
            identity = replace(
                origin,
                operation=MUTATION_POLICY,
                occurrence=origin.occurrence
                + ((origin.task_reference + "/@approval-revision", item["revision"]),),
            )
            if entry[2] != proof.request_digest or _receipt(connection, identity) != proof:
                raise ValueError
            action = item["request"]["action"]
            if action in {"reassign", "escalate"}:
                if item["request"]["value"] is not None:
                    raise ValueError
                targets = (
                    selected.reassignment_targets
                    if action == "reassign"
                    else selected.escalation_targets
                )
                if (
                    item["request"]["actor"] != assignee
                    or item["request"]["assignee"] not in targets
                    or item["event"] is not None
                ):
                    raise ValueError
                assignee = item["request"]["assignee"]
            elif action in {"approved", "rejected", "expired"}:
                if item["request"]["assignee"] is not None:
                    raise ValueError
                if action != "expired":
                    selected.validate_response(item["request"]["value"])
                terminal = item["event"]
                terminal_projection = (
                    item["request"]["actor"],
                    item["operation_key"],
                    item["recorded_at"],
                )
                if (
                    terminal is None
                    or terminal["data"].get("approvalProfile") != PROTECTED_APPROVAL_PROFILE
                    or terminal["data"]["decision"] != action
                    or terminal["subject"] != approval_id
                    or terminal["id"] != item["operation_key"]
                    or terminal["data"]["operator_id"] != item["request"]["actor"]
                    or terminal["data"]["value"] != item["request"]["value"]
                    or terminal["data"]["revision"] != item["revision"]
                ):
                    raise ValueError
                if action == "expired":
                    if (
                        item["request"]["actor"] != "system:approval-expiry"
                        or item["recorded_at"] < value["expires_at"]
                    ):
                        raise ValueError
                elif (
                    item["request"]["actor"] != assignee
                    or item["recorded_at"] >= value["expires_at"]
                ):
                    raise ValueError
            else:
                raise ValueError
            if item["request"]["revision"] != revision:
                raise ValueError
            revision, digest = item["revision"], proof.request_digest
        if (
            tuple(row[2:5]) != (revision, assignee, digest)
            or state[0] != (terminal["data"]["decision"] if terminal is not None else "pending")
            or (json.loads(state[3]) if state[3] is not None else None) != terminal
            or tuple(state[5:]) != terminal_projection
        ):
            raise ValueError
        if terminal is not None:
            retained = connection.execute(
                "SELECT envelope FROM event_inbox WHERE scope=? AND source=? AND event_id=?",
                (value["event_scope"], terminal["source"], terminal["id"]),
            ).fetchone()
            if retained is None or json.loads(retained[0]) != terminal:
                raise ValueError
        return value, (revision, assignee, digest, entries)
    except (ValueError, TypeError, KeyError, IndexError):
        raise _failure() from None


def verify_protected_approval_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    if (
        identity.operation not in {REQUEST_POLICY, MUTATION_POLICY}
        or receipt.owner != _OWNER
        or receipt.status != "completed"
        or not isinstance(receipt.output, dict)
    ):
        raise _failure()
    value, _ = _request_value(connection, invocations, receipt.output["approval_id"])
    origin = _identity(value["origin"])
    expected_identity = replace(origin, operation=identity.operation)
    if identity.operation == MUTATION_POLICY:
        expected_identity = replace(
            expected_identity,
            occurrence=origin.occurrence
            + ((origin.task_reference + "/@approval-revision", receipt.output["revision"]),),
        )
    if (
        identity != expected_identity
        or _receipt(connection, identity) != receipt
        or receipt != _known(receipt.output, identity.operation)
    ):
        raise _failure()


def verify_protected_approval_event(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    event: EventEnvelope,
    scope: str,
) -> None:
    requests, audit = _tables(connection)
    if event.type not in {
        "io.openworkflow.agent.approval.requested",
        "io.openworkflow.agent.approval.decided",
    }:
        return
    protected = isinstance(event.data, dict) and (
        "approvalPolicy" in event.data
        or event.data.get("approvalProfile") == PROTECTED_APPROVAL_PROFILE
    )
    records = "owa_approvals.approvals" if connection.is_postgresql else "approvals"
    if table_exists(connection, records):
        existing = connection.execute(
            f"SELECT CASE WHEN length(request_event)<=1048576 THEN request_event ELSE NULL END "
            f"FROM {records} WHERE approval_id=?",
            (event.subject,),
        ).fetchone()
        if existing is not None:
            if existing[0] is None:
                raise _failure()
            data = json.loads(existing[0]).get("data")
            protected = protected or isinstance(data, dict) and "approvalPolicy" in data
    if not table_exists(connection, requests):
        if protected:
            raise _failure()
        return
    row = connection.execute(
        f"SELECT 1 FROM {requests} WHERE approval_id=?", (event.subject,)
    ).fetchone()
    if row is None:
        if protected or (
            table_exists(connection, audit)
            and connection.execute(
                f"SELECT 1 FROM {audit} WHERE approval_id=? LIMIT 1",
                (event.subject,),
            ).fetchone()
            is not None
        ):
            raise _failure()
        return
    value, state = _request_value(connection, invocations, str(event.subject))
    if value["event_scope"] != scope:
        raise _failure()
    if event.type.endswith(".requested"):
        if event.as_dict() != value["request_event"]:
            raise _failure()
    else:
        entries = state[3]
        if not entries or json.loads(entries[-1][1])["event"] != event.as_dict():
            raise _failure()


class ProtectedApprovalStore:
    def __init__(
        self,
        store: ApprovalStore,
        invocations: InvocationStore,
        *,
        actor_ids: frozenset[str],
        max_pending: int = 1000,
        max_policy_bytes: int = 65536,
    ) -> None:
        self.store, self.connection, self.invocations = store, store.connection, invocations
        self.actor_ids, self.max_pending, self.max_policy_bytes = (
            actor_ids,
            max_pending,
            max_policy_bytes,
        )
        self.requests, self.audit = _tables(self.connection)
        self.connection.execute(
            f"CREATE TABLE IF NOT EXISTS {self.requests}(approval_id TEXT PRIMARY KEY,"
            "invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,"
            "digest TEXT NOT NULL,revision INTEGER NOT NULL,assignee TEXT NOT NULL,"
            "audit_digest TEXT NOT NULL)"
        )
        self.connection.execute(
            f"CREATE TABLE IF NOT EXISTS {self.audit}(approval_id TEXT NOT NULL,"
            "revision INTEGER NOT NULL,operation_key TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,"
            "digest TEXT NOT NULL,PRIMARY KEY(approval_id,revision))"
        )
        self.connection.commit()

    def admit(
        self,
        record: ApprovalRecord,
        event: EventEnvelope,
        origin_identity: EffectIdentity | None,
        request_digest: str | None,
    ) -> None:
        if origin_identity is None or origin_identity.operation != "emit" or request_digest is None:
            raise ApprovalValidationError("protected approvals require an owned workflow request")
        policy = ProtectedApprovalPolicy.from_value(event.data["approvalPolicy"])
        bounded_json(policy.as_value(), self.max_policy_bytes)
        if not {policy.assignee, *policy.reassignment_targets, *policy.escalation_targets}.issubset(
            self.actor_ids
        ):
            raise ApprovalValidationError("approval policy references an unbound actor")
        expiry = original_expiry(event.extensions.get("approvalexpiresat"))
        handle = self.invocations.get(origin_identity.invocation_id)
        if (
            handle is None
            or handle.workflow_fingerprint != origin_identity.workflow_fingerprint
            or handle.owner_principal != record.event_scope
        ):
            raise ApprovalAuthorizationError("approval request origin is inconsistent")
        _stored_evidence(self.connection, self.invocations, handle)
        definition_identity = admission_identity(handle)
        definition = _receipt(self.connection, definition_identity)
        if definition is None:
            raise _failure()
        value = {
            "profile": REQUEST_PROFILE,
            "approval_id": record.approval_id,
            "policy": policy.as_value(),
            "event_scope": record.event_scope,
            "request_event": event.as_dict(),
            "origin": _identity_value(origin_identity),
            "origin_request_digest": request_digest,
            "invocation_binding": _binding(handle),
            "input_digest": effect_request_digest(
                REQUEST_PROFILE, self.invocations.read_input(handle)
            ),
            "definition_receipt": _dependency_value(
                ReceiptDependency.capture(definition_identity, definition)
            ),
            "expires_at": expiry,
            "history_limits": dict(_HISTORY_LIMITS),
        }
        receipt = _known(value, REQUEST_POLICY)
        existing = self.connection.execute(
            f"SELECT payload FROM {self.requests} WHERE approval_id=?", (record.approval_id,)
        ).fetchone()
        if existing is not None:
            if json.loads(existing[0]) != json.loads(bounded_json(value, 524_288)):
                raise ApprovalConflict("protected approval request identity is already bound")
            _request_value(
                self.connection, self.invocations, record.approval_id, allow_unfinished=True
            )
            return
        pending = self.store._execute(
            "SELECT COUNT(*) FROM approvals WHERE status='pending'"
        ).fetchone()[0]
        if pending > self.max_pending:
            raise ApprovalConflict("protected approval queue is full")
        self.connection.execute(
            f"INSERT INTO {self.requests} VALUES(?,?,?,?,?,?,?,?)",
            (
                record.approval_id,
                handle.invocation_id,
                handle.workflow_fingerprint,
                bounded_json(value, 524_288),
                receipt.request_digest,
                1,
                policy.assignee,
                receipt.request_digest,
            ),
        )
        insert_completed_receipt(
            self.connection, replace(origin_identity, operation=REQUEST_POLICY), receipt
        )

    def inspect(self, approval_id: str) -> tuple[dict[str, Any], Any]:
        result = _request_value(self.connection, self.invocations, approval_id)
        policy = ProtectedApprovalPolicy.from_value(result[0]["policy"])
        if not {policy.assignee, *policy.reassignment_targets, *policy.escalation_targets}.issubset(
            self.actor_ids
        ):
            raise ApprovalAuthorizationError("original approval actor binding is unavailable")
        return result

    def verify_invocation(self, invocation_id: str, fingerprint: str) -> None:
        request_count = self.connection.execute(
            f"SELECT COUNT(*) FROM {self.requests} WHERE invocation_id=?", (invocation_id,)
        ).fetchone()[0]
        index_stats = self.connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(payload)),0),COALESCE(MAX(length(payload)),0) "
            "FROM workflow_effect_invocations WHERE invocation_id=?",
            (invocation_id,),
        ).fetchone()
        if (
            request_count > MAX_INVOCATION_ROWS
            or index_stats[0] > MAX_INVOCATION_ROWS
            or index_stats[1] > MAX_INVOCATION_INDEX_BYTES
            or index_stats[2] > MAX_INDEX_PAYLOAD_BYTES
        ):
            raise _failure()
        requests = self.connection.execute(
            "SELECT CASE WHEN length(approval_id)<=1024 THEN approval_id ELSE NULL END "
            f"FROM {self.requests} "
            "WHERE invocation_id=? LIMIT ?",
            (invocation_id, MAX_INVOCATION_ROWS + 1),
        ).fetchall()
        if len(requests) > MAX_INVOCATION_ROWS:
            raise _failure()
        for row in requests:
            if not isinstance(row[0], str):
                raise _failure()
            value, _ = self.inspect(row[0])
            if value["invocation_binding"]["workflow_fingerprint"] != fingerprint:
                raise _failure()
        cursor = self.connection.execute(
            "SELECT CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
            "FROM workflow_effect_invocations WHERE invocation_id=? LIMIT ?",
            (MAX_INDEX_PAYLOAD_BYTES, invocation_id, MAX_INVOCATION_ROWS + 1),
        )
        rows, consumed = 0, 0
        while (row := cursor.fetchone()) is not None:
            rows += 1
            if not isinstance(row[0], str):
                raise _failure()
            consumed += len(row[0].encode("utf-8"))
            if rows > MAX_INVOCATION_ROWS or consumed > MAX_INVOCATION_INDEX_BYTES:
                raise _failure()
            try:
                identity = _identity(json.loads(row[0]))
            except (ValueError, TypeError):
                raise _failure() from None
            if (
                identity.invocation_id != invocation_id
                or identity.workflow_fingerprint != fingerprint
            ):
                raise _failure()
            if identity.operation in {REQUEST_POLICY, MUTATION_POLICY}:
                receipt = _receipt(self.connection, identity)
                if receipt is None:
                    raise _failure()
                verify_protected_approval_receipt(
                    self.connection, self.invocations, identity, receipt
                )
            elif identity.operation == "emit":
                receipt = _receipt(self.connection, identity)
                if receipt is not None and isinstance(receipt.output, dict):
                    data = receipt.output.get("data")
                    if isinstance(data, dict) and "approvalPolicy" in data:
                        from .approvals import _envelope

                        handle = self.invocations.get(invocation_id)
                        if handle is None:
                            raise _failure()
                        verify_protected_approval_event(
                            self.connection,
                            self.invocations,
                            _envelope(receipt.output),
                            handle.owner_principal or "anonymous",
                        )

    def mutate(
        self,
        approval_id: str,
        *,
        action: str,
        actor: str,
        revision: int,
        operation_key: str,
        value: Any = None,
        assignee: str | None = None,
    ) -> EventEnvelope | None:
        # Caller owns the shared approval/inbox transaction. No commit here.
        lock = " FOR UPDATE" if self.connection.is_postgresql else ""
        self.connection.execute(
            f"SELECT approval_id FROM {self.requests} WHERE approval_id=?{lock}", (approval_id,)
        ).fetchone()
        request, state = self.inspect(approval_id)
        current_revision, current_assignee, previous_digest, _ = state
        actor_identity(actor, internal=action == "expired")
        if (
            type(revision) is not int
            or revision < 1
            or not isinstance(operation_key, str)
            or not operation_key
            or len(operation_key) > 256
        ):
            raise ApprovalValidationError(
                "protected approval requires a revision and bounded operation key"
            )
        supplied = {
            "action": action,
            "actor": actor,
            "revision": revision,
            "assignee": assignee,
            "value": value,
        }
        bounded_json(supplied)
        prior = self.connection.execute(
            f"SELECT approval_id,payload FROM {self.audit} WHERE operation_key=?", (operation_key,)
        ).fetchone()
        if prior is not None:
            item = json.loads(prior[1])
            if prior[0] != approval_id or item["request"] != supplied:
                raise ApprovalConflict("approval operation key belongs to different content")
            from .approvals import _envelope

            return _envelope(item["event"]) if item["event"] is not None else None
        record = self.store._execute(
            "SELECT status FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if record[0] != "pending" or revision != current_revision:
            raise ApprovalConflict("approval decision or assignment revision is stale")
        now = float(
            self.connection.execute(
                "SELECT EXTRACT(EPOCH FROM clock_timestamp())"
                if self.connection.is_postgresql
                else "SELECT (julianday('now')-2440587.5)*86400"
            ).fetchone()[0]
        )
        policy = ProtectedApprovalPolicy.from_value(request["policy"])
        if action == "expired":
            if actor != "system:approval-expiry" or now < request["expires_at"]:
                raise ApprovalConflict("original approval expiry is not due")
        elif actor != current_assignee or actor not in self.actor_ids:
            raise ApprovalAuthorizationError("approval actor is not the current assignee")
        elif now >= request["expires_at"]:
            raise ApprovalConflict("original approval expiry has elapsed")
        event = None
        if action in {"reassign", "escalate"}:
            if value is not None:
                raise ApprovalValidationError(
                    "assignment operations do not accept a response value"
                )
            targets = (
                policy.reassignment_targets if action == "reassign" else policy.escalation_targets
            )
            if (
                assignee not in targets
                or assignee not in self.actor_ids
                or revision >= MAX_REASSIGNMENTS
            ):
                raise ApprovalValidationError(
                    "approval assignment target or revision is unsupported"
                )
            selected_assignee = assignee
        elif action in {"approved", "rejected", "expired"}:
            if assignee is not None:
                raise ApprovalValidationError(
                    "terminal decisions do not accept an assignment target"
                )
            if action != "expired":
                policy.validate_response(value)
            selected_assignee = current_assignee
            event = EventEnvelope(
                id=operation_key,
                source="urn:open-workflow-agent:approvals",
                type="io.openworkflow.agent.approval.decided",
                time=datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z"),
                subject=approval_id,
                datacontenttype="application/json",
                data={
                    "approvalProfile": PROTECTED_APPROVAL_PROFILE,
                    "approval_id": approval_id,
                    "decision": action,
                    "operator_id": actor,
                    "value": value,
                    "revision": revision + 1,
                },
                extensions={"approvalid": approval_id},
            )
        else:
            raise ApprovalValidationError("protected approval action is unsupported")
        item = {
            "profile": MUTATION_PROFILE,
            "approval_id": approval_id,
            "revision": revision + 1,
            "previous_digest": previous_digest,
            "operation_key": operation_key,
            "request": supplied,
            "recorded_at": now,
            "event": event.as_dict() if event is not None else None,
        }
        proof = _known(item, MUTATION_POLICY)
        origin = _identity(request["origin"])
        identity = replace(
            origin,
            operation=MUTATION_POLICY,
            occurrence=origin.occurrence
            + ((origin.task_reference + "/@approval-revision", revision + 1),),
        )
        self.connection.execute(
            f"INSERT INTO {self.audit} VALUES(?,?,?,?,?)",
            (
                approval_id,
                revision + 1,
                operation_key,
                bounded_json(item, 524_288),
                proof.request_digest,
            ),
        )
        insert_completed_receipt(self.connection, identity, proof)
        changed = self.connection.execute(
            f"UPDATE {self.requests} SET revision=?,assignee=?,audit_digest=? "
            "WHERE approval_id=? AND revision=?",
            (revision + 1, selected_assignee, proof.request_digest, approval_id, revision),
        ).rowcount
        if changed != 1:
            raise ApprovalConflict("approval assignment changed concurrently")
        if event is not None:
            changed = self.store._execute(
                "UPDATE approvals SET status=?,decision_event=?,operator_id=?,"
                "decision_operation_key=?,decided_at=? WHERE approval_id=? AND status='pending'",
                (action, event.raw(), actor, operation_key, now, approval_id),
            ).rowcount
            if changed != 1:
                raise ApprovalConflict("approval terminal outcome changed concurrently")
        return event
