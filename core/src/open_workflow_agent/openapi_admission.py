"""Immutable OpenAPI operation decisions under common workflow authority."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectReceipt, ReceiptDependency, effect_request_digest
from .errors import InvocationStateConflict, ToolError, WorkflowExecutionError
from .ownership import ExecutionLease, execution_write_guard, owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore
from .recovery_definitions import admission_identity, verify_definition_admission_receipt
from .security import ProfileAuthentication, resolve_secret
from .storage import StorageConnection

if TYPE_CHECKING:
    from .openapi_client import OpenApiOperationSelection
    from .services import RuntimeServices

OPERATION_POLICY = "policy:openapi-operation:v1"
_PROFILE = "owa/openapi-admission/v1"
_MAX_RECORD_BYTES = 8_388_608


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate admission field")
        result[name] = value
    return result


def _encoded(value: Any) -> str:
    effect_request_digest(_PROFILE, value)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(raw.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise ToolError("OpenAPI operation admission exceeds its finite size limit")
    return raw


def _receipt(connection: StorageConnection, identity: EffectIdentity) -> EffectReceipt | None:
    row = connection.execute(
        "SELECT owner,status,CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
        "FROM workflow_effects WHERE identity=?",
        (_MAX_RECORD_BYTES, identity.key),
    ).fetchone()
    index = connection.execute(
        "SELECT invocation_id,fingerprint,CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
        "FROM workflow_effect_invocations "
        "WHERE identity=?",
        (_MAX_RECORD_BYTES, identity.key),
    ).fetchone()
    if row is None and index is None:
        return None
    if (
        row is None
        or index is None
        or row[2] is None
        or index[2] is None
        or max(len(row[2].encode("utf-8")), len(index[2].encode("utf-8"))) > _MAX_RECORD_BYTES
    ):
        raise WorkflowExecutionError("OpenAPI operation receipt evidence is missing")
    try:
        receipt = EffectReceipt(**json.loads(row[2], object_pairs_hook=_unique))
        if (
            tuple(row[:2]) != (receipt.owner, receipt.status)
            or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
            or EffectIdentity.from_value(json.loads(index[2], object_pairs_hook=_unique))
            != identity
        ):
            raise ValueError("inconsistent policy identity")
        return receipt
    except (ValueError, TypeError, KeyError):
        raise WorkflowExecutionError("OpenAPI operation receipt evidence is inconsistent") from None


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


def _verify_record(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> dict[str, Any]:
    from .openapi_client import OpenApiOperationSelection

    try:
        value = receipt.output
        if (
            identity.operation != OPERATION_POLICY
            or invocations is None
            or receipt.owner != "openapi-admission"
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
                "transport_binding",
                "selection",
                "deadline_at",
                "task_deadline_at",
            }
            or value["profile"] != _PROFILE
            or not isinstance(value["request_digest"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["request_digest"]) is None
            or receipt.request_digest != effect_request_digest(OPERATION_POLICY, value)
        ):
            raise ValueError("invalid operation admission")
        business = EffectIdentity.from_value(value["business_identity"])
        if (
            business.operation != "call:openapi"
            or replace(business, operation=OPERATION_POLICY) != identity
        ):
            raise ValueError("invalid action identity")
        row = connection.execute(
            "SELECT invocation_id,fingerprint,CASE WHEN length(payload)<=? THEN payload "
            "ELSE NULL END,digest FROM openapi_operation_admissions "
            "WHERE business_key=?",
            (_MAX_RECORD_BYTES, business.key),
        ).fetchone()
        if (
            row is None
            or tuple(row[:2]) != (business.invocation_id, business.workflow_fingerprint)
            or row[2] is None
            or len(row[2].encode("utf-8")) > _MAX_RECORD_BYTES
            or json.loads(row[2], object_pairs_hook=_unique) != value
            or row[3] != receipt.request_digest
        ):
            raise ValueError("missing immutable operation decision")
        handle = invocations.get(business.invocation_id)
        if handle is None or _invocation_binding(handle) != value["invocation_binding"]:
            raise ValueError("changed invocation binding")
        if value["input_digest"] != effect_request_digest(
            "owa/openapi-input/v1", invocations.read_input(handle)
        ):
            raise ValueError("changed original input")
        dependency = ReceiptDependency.from_value(value["definition_receipt"])
        if dependency.identity != admission_identity(handle):
            raise ValueError("invalid original definition identity")
        definition = _receipt(connection, dependency.identity)
        if (
            definition is None
            or ReceiptDependency.capture(dependency.identity, definition) != dependency
        ):
            raise ValueError("changed definition admission")
        verify_definition_admission_receipt(
            connection, invocations, dependency.identity, definition
        )
        deadline = value["deadline_at"]
        if type(deadline) not in {int, float} or not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("invalid original deadline")
        task_deadline = value["task_deadline_at"]
        if task_deadline is not None and (
            type(task_deadline) not in {int, float}
            or not math.isfinite(task_deadline)
            or task_deadline <= 0
            or deadline > task_deadline
        ):
            raise ValueError("invalid original task deadline")
        selection = OpenApiOperationSelection.from_value(value["selection"])
        authentication = value["transport_binding"]["authentication"]
        if selection.credential_profile != (authentication["name"] if authentication else None):
            raise ValueError("changed credential profile binding")
        return value
    except (ValueError, TypeError, KeyError, ToolError):
        raise WorkflowExecutionError(
            "OpenAPI operation admission evidence is inconsistent"
        ) from None


def verify_openapi_operation_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """Pure saved-evidence verification; no SDK server, lease or receiver writes."""
    indexed = _receipt(connection, identity)
    if indexed != receipt:
        raise WorkflowExecutionError("OpenAPI operation receipt identity is inconsistent")
    _verify_record(connection, invocations, identity, receipt)


def _transport_binding(services: RuntimeServices) -> dict[str, Any]:
    http = services.protocols.http
    authentication = (
        getattr(services.protocols, "_profile_authentication", None) or http.authentication
    )
    if authentication is not None and not isinstance(authentication, ProfileAuthentication):
        raise ToolError("OpenAPI operation requires a named deployment authentication binding")
    if authentication is None and http.client_cert is not None:
        raise ToolError("OpenAPI operation client identity requires a named deployment binding")
    auth = (
        {
            "name": authentication.profile_name,
            "profile": authentication.security.profile(authentication.profile_name).model_dump(
                mode="json"
            ),
        }
        if isinstance(authentication, ProfileAuthentication)
        else None
    )
    if isinstance(http.verify_tls, bool):
        trust: Any = http.verify_tls
    elif isinstance(http.verify_tls, str):
        # A selected SecretReference may resolve to a CA path. Retain the
        # deployment reference, never its resolved environment value.
        profile = (
            authentication.security.profile(authentication.profile_name)
            if isinstance(authentication, ProfileAuthentication)
            else None
        )
        reference = getattr(profile, "ca_bundle", None)
        if reference is None:
            raise ToolError("OpenAPI operation CA requires a named deployment reference")
        try:
            if resolve_secret(reference) != http.verify_tls:
                raise ToolError(
                    "OpenAPI operation applied CA differs from its deployment reference"
                )
        except ValueError:
            raise ToolError("OpenAPI operation deployment CA reference is unavailable") from None
        trust = {"ca_reference": reference.model_dump(mode="json")}
    else:
        raise ToolError("OpenAPI operation TLS context has no declared immutable binding")
    if trust is False or http.follow_redirects:
        raise ToolError("OpenAPI operation requires verified TLS and disabled redirects")
    declared_grants = services.config.protocols.openapi_credential_endpoints
    applied_grants = services.protocols._openapi_credential_endpoints
    if declared_grants != applied_grants:
        raise WorkflowExecutionError("OpenAPI operation applied credential authority changed")
    return {
        "authentication": auth,
        "verify_tls": trust,
        "allowed_hosts": sorted(http.allowed_hosts) if http.allowed_hosts is not None else None,
        "timeout": http.timeout,
        "max_response_bytes": http.max_response_bytes,
        "dns_policy": http.destination_resolver is not None,
        "credential_endpoints": {
            name: list(endpoints) for name, endpoints in sorted(applied_grants.items())
        },
    }


@dataclass(frozen=True, slots=True)
class OpenApiAdmission:
    identity: EffectIdentity
    receipt: EffectReceipt
    selection: OpenApiOperationSelection


class OpenApiOperationAdmission:
    def __init__(self, services: RuntimeServices):
        self.services = services
        self.journal = services.effect_journal
        self.connection = self.journal.connection
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS openapi_operation_admissions "
            "(business_key TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,"
            "fingerprint TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.commit()

    async def prepare(
        self,
        identity: EffectIdentity,
        payload: Any,
        handle: ExecutionHandle,
        *,
        execution_lease: ExecutionLease | None,
        deadline_at: float | None,
        now: Callable[[], float],
        allow_fetch: bool = True,
    ) -> OpenApiAdmission | None:
        from .openapi_client import (
            OpenApiOperationSelection,
            fetch_validate_description,
            prepare_request,
            select_operation,
            validate_operation_authority,
        )

        if not isinstance(payload, Mapping) or "document" not in payload:
            return None
        if (
            identity.operation != "call:openapi"
            or execution_lease is None
            or identity.invocation_id != handle.invocation_id
            or identity.workflow_fingerprint != handle.workflow_fingerprint
            or execution_lease.engine != handle.engine
        ):
            raise InvocationStateConflict("OpenAPI operation execution authority is unavailable")
        operation_identifier = payload.get("operationId")
        if (
            not isinstance(operation_identifier, str)
            or not operation_identifier
            or len(operation_identifier) > 256
        ):
            raise ToolError("OpenAPI operation requires a bounded operationId")
        if (
            execution_lease.invocation_id != handle.invocation_id
            or execution_lease.workflow_fingerprint != handle.workflow_fingerprint
        ):
            raise InvocationStateConflict("OpenAPI operation execution authority is inconsistent")
        current = self.services.invocations.get(handle.invocation_id)
        if (
            current is None
            or current.status != "running"
            or _invocation_binding(current) != _invocation_binding(handle)
        ):
            raise InvocationStateConflict("OpenAPI operation parent state or identity changed")
        guard, parameters = execution_write_guard(self.connection, execution_lease)
        if self.connection.execute(guard, parameters).fetchone() is None:
            raise InvocationStateConflict("OpenAPI operation execution ownership was lost")
        self.services.recovery_definitions.verify_admission(handle)
        request_digest = effect_request_digest("openapi", payload)
        binding = _transport_binding(self.services)
        policy = replace(identity, operation=OPERATION_POLICY)
        prior = _receipt(self.connection, policy)
        if prior is not None:
            value = _verify_record(self.connection, self.services.invocations, policy, prior)
            if (
                value["request_digest"] != request_digest
                or value["transport_binding"] != binding
                or value["task_deadline_at"] != deadline_at
            ):
                raise WorkflowExecutionError(
                    "OpenAPI operation admission request or policy changed"
                )
            selection = OpenApiOperationSelection.from_value(value["selection"])
            remaining = value["deadline_at"] - now()
            if remaining <= 0 and self.journal.get(identity) is None:
                raise ToolError("OpenAPI operation original deadline expired")
            return OpenApiAdmission(policy, prior, replace(selection, remaining_timeout=remaining))
        indexed = self.connection.execute(
            "SELECT 1 FROM openapi_operation_admissions WHERE business_key=?", (identity.key,)
        ).fetchone()
        if indexed is not None or self.journal.get(identity) is not None or not allow_fetch:
            raise WorkflowExecutionError("OpenAPI operation original admission is missing")
        if deadline_at is not None and (
            type(deadline_at) not in {int, float}
            or not math.isfinite(deadline_at)
            or deadline_at <= 0
        ):
            raise ToolError("OpenAPI operation original deadline is invalid")
        http = self.services.protocols.http
        from .protocols import _duration_seconds

        call_timeout = _duration_seconds(payload.get("timeout"))
        budget = min(http.timeout, call_timeout) if call_timeout is not None else http.timeout
        if not math.isfinite(budget) or budget <= 0:
            raise ToolError("OpenAPI operation original timeout is invalid")
        original_deadline = (
            min(now() + budget, deadline_at) if deadline_at is not None else now() + budget
        )
        remaining = original_deadline - now()
        if not math.isfinite(remaining) or remaining <= 0:
            raise ToolError("OpenAPI operation original deadline expired")
        started = time.monotonic()
        authentication = (
            getattr(self.services.protocols, "_profile_authentication", None) or http.authentication
        )
        description = await fetch_validate_description(
            http, payload["document"], timeout=remaining, authentication=None
        )
        selection = select_operation(description, operation_identifier)
        prepare_request(selection, payload.get("parameters", {}))
        profile = (
            authentication.profile_name
            if isinstance(authentication, ProfileAuthentication)
            else None
        )
        selection = replace(selection, credential_profile=profile)
        validate_operation_authority(
            selection,
            payload.get("parameters", {}),
            authentication=authentication,
            credential_endpoints=self.services.protocols._openapi_credential_endpoints,
        )
        if http is not self.services.protocols.http or _transport_binding(self.services) != binding:
            raise WorkflowExecutionError(
                "OpenAPI operation deployment binding changed during admission"
            )
        remaining -= time.monotonic() - started
        remaining = min(remaining, original_deadline - now())
        if remaining <= 0:
            raise ToolError("OpenAPI operation original deadline expired")
        definition_identity = admission_identity(handle)
        definition = self.journal.get(definition_identity)
        if definition is None:
            raise WorkflowExecutionError(
                "OpenAPI operation original definition admission is missing"
            )
        dependency = ReceiptDependency.capture(definition_identity, definition)
        value = {
            "profile": _PROFILE,
            "business_identity": json.loads(json.dumps(asdict(identity))),
            "request_digest": request_digest,
            "input_digest": effect_request_digest(
                "owa/openapi-input/v1", self.services.invocations.read_input(handle)
            ),
            "invocation_binding": _invocation_binding(handle),
            "definition_receipt": json.loads(json.dumps(asdict(dependency))),
            "transport_binding": binding,
            "selection": selection.as_value(),
            "deadline_at": original_deadline,
            "task_deadline_at": deadline_at,
        }
        receipt = EffectReceipt(
            effect_request_digest(OPERATION_POLICY, value),
            "openapi-admission",
            status="completed",
            output=value,
        )
        with owned_write_transaction(
            self.connection,
            execution_lease,
            invocation_id=identity.invocation_id,
            workflow_fingerprint=identity.workflow_fingerprint,
        ):
            self.services.recovery_definitions.verify_admission(handle)
            self.connection.execute(
                "INSERT INTO openapi_operation_admissions "
                "(business_key,invocation_id,fingerprint,payload,digest) VALUES(?,?,?,?,?) "
                "ON CONFLICT(business_key) DO NOTHING",
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
        return OpenApiAdmission(policy, receipt, replace(selection, remaining_timeout=remaining))
