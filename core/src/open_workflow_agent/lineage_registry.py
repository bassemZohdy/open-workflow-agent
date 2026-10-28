"""Private common lineage evidence and fail-closed replay effect policy.

This registry does not create invocations or attest engine-native forks. Its
caller must verify the native proof before binding it, and native continuation
must require the binding before adopting remapped receipt dependencies.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

from .effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
    effect_request_digest,
)
from .execution_authority import ExecutionAuthority
from .execution_authorization import ExecutionAuthorizationStore, decision_identity
from .ownership import execution_write_guard
from .persistence import ExecutionHandle, InvocationStore, _verify_identity
from .state_lineage import StateLineageRequest, _json, lineage_error

PROFILE = "owa/lineage-binding/v1"
OPERATION = "policy:lineage-binding:v1"
MAX_DEPENDENCIES = 64
_HEX = re.compile(r"[a-f0-9]{64}")
_CURRENT_LINEAGE: ContextVar[LineageRegistry | None] = ContextVar("owa_lineage", default=None)


@contextmanager
def lineage_scope(registry: LineageRegistry) -> Iterator[None]:
    """A trusted continuation requires lineage checks for every action."""
    if not isinstance(registry, LineageRegistry):
        raise lineage_error("lineage_scope_invalid")
    token = _CURRENT_LINEAGE.set(registry)
    try:
        yield
    finally:
        _CURRENT_LINEAGE.reset(token)


def current_lineage() -> LineageRegistry | None:
    return _CURRENT_LINEAGE.get()


def lineage_identity(target: ExecutionHandle) -> EffectIdentity:
    return EffectIdentity(
        target.invocation_id, target.workflow_fingerprint, "/do", (("/do", 1),), OPERATION
    )


@dataclass(frozen=True, slots=True)
class RemappedReceipt:
    """A target coordinate backed by an existing, immutable source receipt."""

    target: EffectIdentity
    source: ReceiptDependency

    @classmethod
    def capture(
        cls, request: StateLineageRequest, dependency: ReceiptDependency
    ) -> RemappedReceipt:
        identity = dependency.identity
        if (
            identity.invocation_id != request.source_invocation_id
            or identity.workflow_fingerprint != request.workflow_fingerprint
            or not (
                identity.operation.startswith(("call:", "run:"))
                or identity.operation in {"emit", "listen"}
            )
        ):
            raise lineage_error("receipt_origin_invalid")
        return cls(replace(identity, invocation_id=request.target_invocation_id), dependency)

    def verify(self, request: StateLineageRequest, journal: EffectJournal) -> None:
        if self != self.capture(request, self.source):
            raise lineage_error("receipt_mapping_changed")
        self.source.verify(journal)

    @property
    def value(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(json.dumps({"target": asdict(self.target), "source": asdict(self.source)})),
        )

    @classmethod
    def from_value(cls, value: Any) -> RemappedReceipt:
        if type(value) is not dict or set(value) != {"target", "source"}:
            raise lineage_error("receipt_mapping_invalid")
        return cls(
            EffectIdentity.from_value(value["target"]),
            ReceiptDependency.from_value(value["source"]),
        )


class LineageRegistry:
    """Persist a target-bound common record without copying business receipts."""

    def __init__(
        self,
        journal: EffectJournal,
        invocations: InvocationStore,
        authorizations: ExecutionAuthorizationStore,
        native_proof_verifier: Callable[[StateLineageRequest, str], bool],
    ) -> None:
        if (
            journal._invocations is not invocations
            or authorizations.journal is not journal
            or authorizations.invocations is not invocations
            or not callable(native_proof_verifier)
        ):
            raise ValueError("lineage requires the original common services")
        self.journal, self.invocations, self.authorizations = journal, invocations, authorizations
        self.native_proof_verifier = native_proof_verifier

    def _handles(
        self, request: StateLineageRequest, target: ExecutionHandle
    ) -> tuple[ExecutionHandle, ExecutionHandle]:
        source = self.invocations.get(request.source_invocation_id)
        saved = self.invocations.get(request.target_invocation_id)
        if (
            source is None
            or saved is None
            or target._execution_lease is None
            or source.owner_principal is None
            or source.owner_principal != target.owner_principal
            or saved._persisted_payload is None
            or target._persisted_payload != saved._persisted_payload
            or target.status != saved.status
            or source.workflow_fingerprint != request.workflow_fingerprint
            or saved.workflow_fingerprint != request.workflow_fingerprint
            or target.invocation_id != request.target_invocation_id
            or target.engine != source.engine
            or saved.engine != source.engine
        ):
            raise lineage_error("lineage_identity_invalid")
        _verify_identity(target, json.loads(saved._persisted_payload))
        return source, saved

    def bind(
        self,
        request: StateLineageRequest,
        target: ExecutionHandle,
        authority: ExecutionAuthority,
        native_proof_digest: str,
        dependencies: tuple[ReceiptDependency, ...],
    ) -> EffectReceipt:
        """Record only a caller-verified native proof and existing source receipts."""
        source, _ = self._handles(request, target)
        if (
            not self.authorizations.config_provider().enabled
            or target.status != "running"
            or authority is None
            or authority.principal.identity != source.owner_principal
            or type(native_proof_digest) is not str
            or not _HEX.fullmatch(native_proof_digest)
            or type(dependencies) is not tuple
            or len(dependencies) > MAX_DEPENDENCIES
            or any(type(item) is not ReceiptDependency for item in dependencies)
        ):
            raise lineage_error("lineage_binding_invalid")
        self._verify_native(request, native_proof_digest)
        action, resource = "state." + request.kind, "invocation:" + source.invocation_id
        self.authorizations.authorize_request(authority, action, resource)
        original = self.authorizations.verify_invocation(source)
        if original is None or original.principal.identity != authority.principal.identity:
            raise lineage_error("original_authority_required")
        self.authorizations.authorize_request(original, action, resource)
        mapped = tuple(RemappedReceipt.capture(request, item) for item in dependencies)
        if len({item.target.key for item in mapped}) != len(mapped):
            raise lineage_error("receipt_mapping_duplicate")
        for item in mapped:
            item.verify(request, self.journal)
        record = {
            "profile": PROFILE,
            "request": request.descriptor,
            "native_proof_digest": native_proof_digest,
            "receipts": [item.value for item in mapped],
        }
        _json(record, 262_144)
        digest = effect_request_digest(PROFILE, record)
        marker = lineage_identity(target)
        index = self.journal.connection.execute(
            "SELECT 1 FROM workflow_effect_invocations WHERE identity=?", (marker.key,)
        ).fetchone()
        if self.journal.get(marker) is None and index is not None:
            raise lineage_error("lineage_binding_orphaned")
        decisions = self.journal.connection.execute(
            "SELECT decision_key FROM execution_authorization_decisions "
            "WHERE business_key=? LIMIT 2",
            (marker.key,),
        ).fetchall()
        expected_decision = decision_identity(marker, action, resource).key
        if len(decisions) > 1 or (decisions and decisions[0][0] != expected_decision):
            raise lineage_error("lineage_decision_changed")
        decision = self.authorizations.authorize(
            target, marker, action, resource, digest, execution_lease=target._execution_lease
        )
        if decision is None:
            raise lineage_error("authorization_required")
        result = self.journal.record_value(
            marker, digest, lambda: record, execution_lease=target._execution_lease
        )
        self.read(target)
        return result

    def _verify_native(self, request: StateLineageRequest, digest: str) -> None:
        try:
            if self.native_proof_verifier(request, digest) is not True:
                raise lineage_error("native_proof_unavailable")
        except Exception:
            raise lineage_error("native_proof_unavailable") from None

    def read(
        self, target: ExecutionHandle
    ) -> tuple[StateLineageRequest, tuple[RemappedReceipt, ...], str]:
        """Require and verify the immutable record and every referenced receipt."""
        try:
            identity = lineage_identity(target)
            receipt = self.journal.get(identity)
            if receipt is None or receipt.status != "completed" or receipt.owner != "policy":
                raise lineage_error("lineage_binding_missing")
            index = self.journal.connection.execute(
                "SELECT invocation_id,fingerprint,"
                "CASE WHEN length(payload)<=8192 THEN payload ELSE NULL END "
                "FROM workflow_effect_invocations WHERE identity=?",
                (identity.key,),
            ).fetchone()
            if (
                index is None
                or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
                or type(index[2]) is not str
                or EffectIdentity.from_value(json.loads(index[2])) != identity
            ):
                raise lineage_error("lineage_index_changed")
            value = receipt.output
            if (
                type(value) is not dict
                or set(value) != {"profile", "request", "native_proof_digest", "receipts"}
                or value["profile"] != PROFILE
            ):
                raise lineage_error("lineage_binding_changed")
            _json(value, 262_144)
            if receipt.request_digest != effect_request_digest(PROFILE, value):
                raise lineage_error("lineage_binding_changed")
            request = StateLineageRequest.from_descriptor(value["request"])
            source, _ = self._handles(request, target)
            original = self.authorizations.verify_invocation(source)
            target_authority = self.authorizations.verify_invocation(target)
            if original is None or target_authority is None:
                raise lineage_error("original_authority_required")
            self.authorizations.authorize_request(
                original, "state." + request.kind, "invocation:" + source.invocation_id
            )
            marker = value["native_proof_digest"]
            if (
                type(marker) is not str
                or not _HEX.fullmatch(marker)
                or type(value["receipts"]) is not list
                or len(value["receipts"]) > MAX_DEPENDENCIES
            ):
                raise lineage_error("lineage_binding_changed")
            self._verify_native(request, marker)
            decision = self.authorizations.inspect_decision(
                identity,
                "state." + request.kind,
                "invocation:" + source.invocation_id,
                receipt.request_digest,
            ).receipt
            if decision is None or decision.status != "completed":
                raise lineage_error("lineage_authority_missing")
            mapped = tuple(RemappedReceipt.from_value(item) for item in value["receipts"])
            if len({item.target.key for item in mapped}) != len(mapped):
                raise lineage_error("receipt_mapping_duplicate")
            for item in mapped:
                item.verify(request, self.journal)
            return request, mapped, marker
        except Exception as error:
            from .errors import OwaError

            if isinstance(error, OwaError):
                raise
            raise lineage_error("lineage_binding_unavailable") from None

    def authorize_effect(self, target: ExecutionHandle, identity: EffectIdentity) -> None:
        """Fail closed for any external effect on a lineage-bound read-only run."""
        request, _, _ = self.read(target)
        if target.status != "running":
            raise lineage_error("effect_invocation_not_running")
        assert target._execution_lease is not None
        guard, parameters = execution_write_guard(self.journal.connection, target._execution_lease)
        if self.journal.connection.execute(guard, parameters).fetchone() is None:
            raise lineage_error("effect_ownership_lost")
        if (
            identity.invocation_id != target.invocation_id
            or identity.workflow_fingerprint != target.workflow_fingerprint
        ):
            raise lineage_error("effect_identity_invalid")
        if request.effect_policy == "read_only":
            raise lineage_error("read_only_effect_denied")
