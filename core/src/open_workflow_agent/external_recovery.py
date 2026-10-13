"""Opt-in trusted HTTP outcome lookup, never generic pending-action adoption."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .errors import ConfigurationError, InvocationStateConflict, ToolError, WorkflowExecutionError
from .ownership import ExecutionLease, owned_write_transaction
from .persistence import InvocationStore
from .protocols import HttpClient, _endpoint_value
from .security import ProfileAuthentication
from .storage import StorageConnection

ADMISSION_OPERATION = "policy:external-action-admission:v1"
RECOVERY_OPERATION = "policy:external-action-recovery:v1"
HTTP_RECEIVER_PROFILE = "owa/http-outcome-lookup/v1"
_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _json(value: Any) -> str:
    effect_request_digest("owa/external-json/v1", value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _identity_value(identity: EffectIdentity) -> dict[str, Any]:
    # The immutable internal path uses tuples; its stored JSON representation
    # uses arrays. Normalize only typed identity fields, never user payloads.
    value = asdict(identity)
    value["occurrence"] = [list(entry) for entry in identity.occurrence]
    return value


def canonical_endpoint(value: str) -> str:
    """Pin an exact credential-free HTTPS service route, not a host wildcard."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("invalid route")
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        authority = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
        return urlunsplit(("https", authority, parsed.path or "/", "", ""))
    except (ValueError, TypeError):
        raise ConfigurationError("external recovery requires an exact HTTPS endpoint") from None


def receiver_request_digest(method: str, endpoint: str, body: Any) -> str:
    """Receiver-computable identity of the actual finite-profile business request."""
    return effect_request_digest(
        "owa/http-receiver-request/v1",
        {
            "method": method.upper(),
            "endpoint": canonical_endpoint(endpoint),
            "body": body,
        },
    )


def _running_authority(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    lease: ExecutionLease | None,
) -> None:
    # The ownership lock precedes this invocation lock, matching common state
    # writers. A business cancellation cannot cross this check and the commit;
    # same-generation concurrent admissions/reconciliations are serialized too.
    lock = " FOR UPDATE" if connection.is_postgresql else ""
    row = connection.execute(
        "SELECT payload,status FROM invocations WHERE invocation_id=?" + lock,
        (identity.invocation_id,),
    ).fetchone()
    parent = invocations.get(identity.invocation_id)
    if (
        lease is None
        or row is None
        or row[1] != "running"
        or parent is None
        or parent._persisted_payload != row[0]
        or parent.workflow_fingerprint != identity.workflow_fingerprint
        or lease.engine != parent.engine
    ):
        raise InvocationStateConflict("external recovery requires running invocation authority")


@dataclass(frozen=True, slots=True)
class TrustedHttpReceiver:
    """A deployment asserts this receiver durably deduplicates and retains outcomes.

    This declaration itself does not establish the receiver's behavior. The
    finite acceptance fixture must prove its business commit/outcome contract.
    References identify credentials/principals; they never contain credentials.
    """

    id: str
    business_endpoint: str
    lookup_endpoint: str
    credential_reference: str
    principal_reference: str
    contract_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, str) or not value or len(value) > 256
                for value in (
                    self.id,
                    self.credential_reference,
                    self.principal_reference,
                )
            )
            or self.contract_version != "1.0.0"
        ):
            raise ConfigurationError("external receiver contract references are invalid")
        object.__setattr__(self, "business_endpoint", canonical_endpoint(self.business_endpoint))
        object.__setattr__(self, "lookup_endpoint", canonical_endpoint(self.lookup_endpoint))


@dataclass(frozen=True, slots=True)
class ExternalOutcomeDependency:
    """Immutable common trust/outcome digests, never SDK state or a credential."""

    admission_digest: str
    outcome_digest: str

    def __post_init__(self) -> None:
        import re

        if any(
            not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in (self.admission_digest, self.outcome_digest)
        ):
            raise ValueError("external outcome dependency is invalid")

    @classmethod
    def from_value(cls, value: Any) -> ExternalOutcomeDependency:
        if not isinstance(value, dict) or set(value) != {"admission_digest", "outcome_digest"}:
            raise ValueError("external outcome dependency fields are invalid")
        return cls(**value)


def _read_receipt(connection: StorageConnection, identity: EffectIdentity) -> EffectReceipt | None:
    """Read common storage without recursively calling policy verification."""
    row = connection.execute(
        "SELECT owner,status,payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()
    if row is None:
        return None
    value = json.loads(row[2])
    if value.get("external_outcome") is not None:
        value["external_outcome"] = ExternalOutcomeDependency.from_value(value["external_outcome"])
    receipt = EffectReceipt(**value)
    index = connection.execute(
        "SELECT invocation_id,fingerprint,payload FROM workflow_effect_invocations "
        "WHERE identity=?",
        (identity.key,),
    ).fetchone()
    if (
        (receipt.owner, receipt.status) != tuple(row[:2])
        or index is None
        or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
        or EffectIdentity.from_value(json.loads(index[2])) != identity
    ):
        raise WorkflowExecutionError("external action receipt index is inconsistent")
    return receipt


def _admission(
    connection: StorageConnection, invocations: InvocationStore | None, identity: EffectIdentity
) -> tuple[dict[str, Any], str]:
    row = connection.execute(
        "SELECT invocation_id,fingerprint,payload,digest FROM external_http_admissions "
        "WHERE identity=?",
        (identity.key,),
    ).fetchone()
    if row is None or invocations is None:
        raise WorkflowExecutionError("external action trusted admission is absent")
    value = json.loads(row[2])
    policy = replace(identity, operation=ADMISSION_OPERATION)
    receipt = _read_receipt(connection, policy)
    if (
        identity.operation != "call:http"
        or tuple(row[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
        or not isinstance(value, dict)
        or set(value)
        != {
            "profile",
            "identity",
            "receiver",
            "request_digest",
            "wire_request_digest",
            "operation_id",
            "owner",
            "caller_principal",
            "input_digest",
        }
        or value["profile"] != "owa/external-action-admission/v1"
        or EffectIdentity.from_value(value["identity"]) != identity
        or row[3] != effect_request_digest(ADMISSION_OPERATION, value)
        or receipt is None
        or receipt.status != "completed"
        or receipt.owner != "external-recovery"
        or receipt.request_digest != row[3]
        or receipt.output != value
        or receipt.external_outcome is not None
    ):
        raise WorkflowExecutionError("external action trusted admission is inconsistent")
    parent = invocations.get(identity.invocation_id)
    if (
        parent is None
        or parent.workflow_fingerprint != identity.workflow_fingerprint
        or parent.owner_principal != value["caller_principal"]
        or effect_request_digest("owa/external-original-input/v1", invocations.read_input(parent))
        != value["input_digest"]
    ):
        raise WorkflowExecutionError("external action invocation or input is inconsistent")
    intent = _read_receipt(connection, identity)
    if intent is None or (intent.owner, intent.operation_id, intent.request_digest) != (
        value["owner"],
        value["operation_id"],
        value["request_digest"],
    ):
        raise WorkflowExecutionError("external action original intent is inconsistent")
    return value, row[3]


def _outcome(value: Any, admission: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "profile",
            "receiver_id",
            "contract_version",
            "principal_reference",
            "operation_id",
            "wire_request_digest",
            "status",
            "output",
            "error",
            "error_status_code",
        }
        or value["profile"] != HTTP_RECEIVER_PROFILE
        or value["receiver_id"] != admission["receiver"]["id"]
        or value["contract_version"] != admission["receiver"]["contract_version"]
        or value["principal_reference"] != admission["receiver"]["principal_reference"]
        or value["operation_id"] != admission["operation_id"]
        or value["wire_request_digest"] != admission["wire_request_digest"]
        or value["status"] not in {"completed", "faulted"}
    ):
        raise WorkflowExecutionError("external receiver outcome does not match original request")
    # Reuse the common error/null/value contract rather than accepting a new
    # receiver-specific public error or checkpoint shape.
    EffectReceipt(
        admission["request_digest"],
        admission["owner"],
        status=value["status"],
        output=value["output"],
        error=value["error"],
        error_status_code=value["error_status_code"],
        operation_id=admission["operation_id"],
    )
    return dict(value)


def verify_external_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """Native references must still point to intact common admission/outcome proof."""
    run = replace(identity, operation="call:http")
    admission, admission_digest = _admission(connection, invocations, run)
    if identity.operation == ADMISSION_OPERATION:
        if receipt.output != admission or receipt.request_digest != admission_digest:
            raise WorkflowExecutionError("external admission receipt changed")
        return
    row = connection.execute(
        "SELECT payload,digest FROM external_http_outcomes WHERE identity=?", (run.key,)
    ).fetchone()
    if row is None:
        raise WorkflowExecutionError("external immutable outcome proof is absent")
    proof = _outcome(json.loads(row[0]), admission)
    if row[1] != effect_request_digest("owa/external-outcome-proof/v1", proof):
        raise WorkflowExecutionError("external immutable outcome proof changed")
    resolved = _read_receipt(connection, run)
    dependency = ExternalOutcomeDependency(admission_digest, row[1])
    if (
        resolved is None
        or resolved.external_outcome != dependency
        or (resolved.status, resolved.output, resolved.error, resolved.error_status_code)
        != (proof["status"], proof["output"], proof["error"], proof["error_status_code"])
    ):
        raise WorkflowExecutionError("external action outcome differs from immutable proof")
    recovery = _read_receipt(connection, replace(run, operation=RECOVERY_OPERATION))
    value = {
        "profile": "owa/external-action-recovery/v1",
        "action_key": run.key,
        "admission_digest": admission_digest,
        "outcome_digest": row[1],
    }
    if (
        recovery is None
        or recovery.owner != "external-recovery"
        or recovery.status != "completed"
        or recovery.output != value
        or recovery.request_digest != effect_request_digest(RECOVERY_OPERATION, value)
    ):
        raise WorkflowExecutionError("external reconciliation receipt is inconsistent")
    if identity.operation == RECOVERY_OPERATION:
        if receipt != recovery:
            raise WorkflowExecutionError("external reconciliation receipt changed")
    elif identity.operation != "call:http" or receipt != resolved:
        raise WorkflowExecutionError("external outcome dependency belongs to another operation")


class HttpExternalActionRecovery:
    """Trusted exact-route lookup using the same bounded authenticated HTTP client."""

    def __init__(
        self,
        journal: EffectJournal,
        invocations: InvocationStore,
        http: HttpClient,
        receivers: tuple[TrustedHttpReceiver, ...],
    ) -> None:
        self.journal, self.invocations, self.http = journal, invocations, http
        self.receivers = receivers
        self.connection = journal.connection
        self._lock = threading.RLock()
        if len({r.business_endpoint for r in receivers}) != len(receivers) or len(
            {r.id for r in receivers}
        ) != len(receivers):
            raise ConfigurationError("external receiver routes and identities must be unique")
        for receiver in receivers:
            self._receiver(receiver)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS external_http_admissions (identity TEXT PRIMARY KEY,"
            "invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,"
            "payload TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS external_http_outcomes (identity TEXT PRIMARY KEY,"
            "payload TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.commit()

    def _receiver(self, receiver: TrustedHttpReceiver) -> dict[str, Any]:
        client = self.http
        if (
            isinstance(client.timeout, bool)
            or not isinstance(client.timeout, (int, float))
            or not math.isfinite(client.timeout)
            or not 0 < client.timeout <= 300
            or type(client.max_response_bytes) is not int
            or not 0 < client.max_response_bytes <= 4_000_000
            or not client.verify_tls
            or client.follow_redirects
        ):
            raise ConfigurationError("external lookup requires bounded HTTP, TLS and no redirects")
        authentication: dict[str, Any] | None = None
        if isinstance(client.authentication, ProfileAuthentication):
            auth = client.authentication
            if auth.profile_name != receiver.credential_reference:
                raise ConfigurationError("external lookup credential reference changed")
            profile = auth.security.profile(auth.profile_name)
            if getattr(profile, "principal", None) not in {None, receiver.principal_reference}:
                raise ConfigurationError("external lookup principal reference changed")
            authentication = {
                "profile_name": auth.profile_name,
                "definition": profile.model_dump(mode="json"),
            }
        elif client.authentication is not None or receiver.credential_reference != "none":
            raise ConfigurationError(
                "external lookup requires a declared named authentication profile"
            )
        if client.client_cert is not None and (
            authentication is None or authentication["definition"].get("type") != "mtls"
        ):
            raise ConfigurationError("external lookup mTLS requires named credential references")
        client._check_endpoint(receiver.business_endpoint)
        client._check_endpoint(receiver.lookup_endpoint)
        return {
            "profile": HTTP_RECEIVER_PROFILE,
            **asdict(receiver),
            "http": {
                "timeout": client.timeout,
                "max_response_bytes": client.max_response_bytes,
                "verify_tls": client.verify_tls,
                "follow_redirects": client.follow_redirects,
                "allowed_hosts": sorted(client.allowed_hosts)
                if client.allowed_hosts is not None
                else None,
                "authentication": authentication,
                # Certificate/key material or resolved secret values are never
                # persisted; named mTLS source references are pinned above.
                "mtls_enabled": client.client_cert is not None,
            },
        }

    def _select(self, identity: EffectIdentity, payload: Any) -> TrustedHttpReceiver | None:
        if identity.operation != "call:http" or not isinstance(payload, dict):
            return None
        endpoint = _endpoint_value(payload.get("endpoint") or payload.get("url"))
        if not isinstance(endpoint, str):
            return None
        try:
            endpoint = canonical_endpoint(endpoint)
        except ConfigurationError:
            return None
        if (
            str(payload.get("method", "GET")).upper() not in _METHODS
            or payload.get("headers") not in (None, {})
            or payload.get("query") not in (None, {})
            or payload.get("redirect") not in (None, False)
            or payload.get("output", "content") != "content"
        ):
            return None
        return next((r for r in self.receivers if r.business_endpoint == endpoint), None)

    def _authority(self, identity: EffectIdentity, lease: ExecutionLease | None) -> None:
        _running_authority(self.connection, self.invocations, identity, lease)

    def admit(
        self,
        identity: EffectIdentity,
        payload: Any,
        *,
        owner: str,
        operation_id: str,
        execution_lease: ExecutionLease | None,
    ) -> tuple[EffectReceipt, EffectReceipt] | None:
        receiver = self._select(identity, payload)
        if receiver is None:
            return None
        if (
            not isinstance(operation_id, str)
            or not operation_id
            or len(operation_id) > 1024
            or (payload.get("operation_id") or payload.get("operationId") or payload.get("id"))
            != operation_id
        ):
            raise WorkflowExecutionError("external action operation key is invalid")
        digest = effect_request_digest("http", payload)
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                execution_lease,
                invocation_id=identity.invocation_id,
                workflow_fingerprint=identity.workflow_fingerprint,
            ),
        ):
            self._authority(identity, execution_lease)
            existing = self.journal.get(identity)
            if existing is not None:
                trusted = self.connection.execute(
                    "SELECT 1 FROM external_http_admissions WHERE identity=?", (identity.key,)
                ).fetchone()
                if trusted is None:
                    policy_identity = replace(identity, operation=ADMISSION_OPERATION)
                    if (
                        self.connection.execute(
                            "SELECT 1 FROM workflow_effect_invocations WHERE identity=?",
                            (policy_identity.key,),
                        ).fetchone()
                        is not None
                    ):
                        raise WorkflowExecutionError("external trusted admission is absent")
                    # A receiver declaration cannot retroactively certify an
                    # intent that was dispatched under another trust profile.
                    return None
                value, _ = _admission(self.connection, self.invocations, identity)
                self._validate_current(value, receiver, payload, operation_id)
                policy = self.journal.get(replace(identity, operation=ADMISSION_OPERATION))
                if policy is None:
                    raise WorkflowExecutionError("external trusted admission receipt is absent")
                return existing, policy
            if (
                self.connection.execute(
                    "SELECT 1 FROM external_http_admissions WHERE identity=?", (identity.key,)
                ).fetchone()
                is not None
            ):
                raise WorkflowExecutionError("external original intent is absent")
            parent = self.invocations.get(identity.invocation_id)
            assert parent is not None
            value = {
                "profile": "owa/external-action-admission/v1",
                "identity": _identity_value(identity),
                "receiver": self._receiver(receiver),
                "request_digest": digest,
                "wire_request_digest": receiver_request_digest(
                    str(payload.get("method", "GET")),
                    receiver.business_endpoint,
                    payload.get("body"),
                ),
                "operation_id": operation_id,
                "owner": owner,
                "caller_principal": parent.owner_principal,
                "input_digest": effect_request_digest(
                    "owa/external-original-input/v1", self.invocations.read_input(parent)
                ),
            }
            admission_digest = effect_request_digest(ADMISSION_OPERATION, value)
            self.connection.execute(
                "INSERT INTO external_http_admissions"
                "(identity,invocation_id,fingerprint,payload,digest) VALUES (?,?,?,?,?)",
                (
                    identity.key,
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    _json(value),
                    admission_digest,
                ),
            )
            intent = EffectReceipt(digest, owner, operation_id=operation_id)
            self.connection.execute(
                "INSERT INTO workflow_effect_invocations"
                "(identity,invocation_id,fingerprint,payload) VALUES (?,?,?,?)",
                (
                    identity.key,
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    _json(_identity_value(identity)),
                ),
            )
            self.connection.execute(
                "INSERT INTO workflow_effects(identity,owner,status,payload) VALUES (?,?,?,?)",
                (
                    identity.key,
                    owner,
                    "pending",
                    _json(
                        {
                            k: v
                            for k, v in asdict(intent).items()
                            if k not in {"child_invocation", "external_outcome"}
                        }
                    ),
                ),
            )
            policy = EffectReceipt(
                admission_digest, "external-recovery", status="completed", output=value
            )
            insert_completed_receipt(
                self.connection, replace(identity, operation=ADMISSION_OPERATION), policy
            )
            _admission(self.connection, self.invocations, identity)
            return intent, policy

    def _validate_current(
        self,
        value: dict[str, Any],
        receiver: TrustedHttpReceiver,
        payload: Any,
        operation_id: str | None,
    ) -> None:
        if (
            value["receiver"] != self._receiver(receiver)
            or value["request_digest"] != effect_request_digest("http", payload)
            or value["operation_id"] != operation_id
            or value["wire_request_digest"]
            != receiver_request_digest(
                str(payload.get("method", "GET")), receiver.business_endpoint, payload.get("body")
            )
        ):
            raise WorkflowExecutionError("external recovery request or trusted binding changed")

    async def recover(
        self, identity: EffectIdentity, payload: Any, *, execution_lease: ExecutionLease | None
    ) -> tuple[EffectReceipt, EffectReceipt] | None:
        receiver = self._select(identity, payload)
        if receiver is None:
            return None
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                execution_lease,
                invocation_id=identity.invocation_id,
                workflow_fingerprint=identity.workflow_fingerprint,
            ),
        ):
            self._authority(identity, execution_lease)
            value, _ = _admission(self.connection, self.invocations, identity)
            intent = self.journal.get(identity)
            if intent is None:
                raise WorkflowExecutionError("external original intent is absent")
            self._validate_current(value, receiver, payload, intent.operation_id)
            if intent.status != "pending":
                policy = self.journal.get(replace(identity, operation=RECOVERY_OPERATION))
                return (intent, policy) if policy is not None else None
        try:
            proof = await self.http.request(
                "GET",
                receiver.lookup_endpoint,
                params={"operation_id": value["operation_id"]},
                operation_id=value["operation_id"],
                follow_redirects=False,
                output="content",
            )
        except ToolError as error:
            if error.details.get("status") == 404:
                return None
            raise WorkflowExecutionError("external outcome lookup failed") from None
        if isinstance(proof, dict) and proof.get("status") in {"unknown", "pending"}:
            return None
        proof = _outcome(proof, value)
        # The receiver is trusted for the declared version; immutable retained
        # evidence is still checked on every known-outcome/native reference read.
        return self.journal.finish_external(
            identity,
            payload,
            receiver=self._receiver(receiver),
            proof=proof,
            execution_lease=execution_lease,
        )


def finish_external(
    journal: EffectJournal,
    identity: EffectIdentity,
    payload: Any,
    *,
    receiver: dict[str, Any],
    proof: dict[str, Any],
    execution_lease: ExecutionLease | None,
) -> tuple[EffectReceipt, EffectReceipt]:
    if identity.operation != "call:http" or execution_lease is None or journal._invocations is None:
        raise InvocationStateConflict("external reconciliation requires HTTP invocation authority")
    connection = journal.connection
    with owned_write_transaction(
        connection,
        execution_lease,
        invocation_id=identity.invocation_id,
        workflow_fingerprint=identity.workflow_fingerprint,
    ):
        _running_authority(connection, journal._invocations, identity, execution_lease)
        value, admission_digest = _admission(connection, journal._invocations, identity)
        if value["receiver"] != receiver or value["request_digest"] != effect_request_digest(
            "http", payload
        ):
            raise WorkflowExecutionError("external reconciliation binding changed")
        outcome = _outcome(proof, value)
        digest = effect_request_digest("owa/external-outcome-proof/v1", outcome)
        recorded = journal.get(identity)
        if recorded is None:
            raise WorkflowExecutionError("external original intent is absent")
        if recorded.status != "pending":
            known = journal.get(replace(identity, operation=RECOVERY_OPERATION))
            if known is None or recorded.external_outcome != ExternalOutcomeDependency(
                admission_digest, digest
            ):
                raise WorkflowExecutionError("external receiver immutable outcome changed")
            return recorded, known
        connection.execute(
            "INSERT INTO external_http_outcomes(identity,payload,digest) VALUES (?,?,?)",
            (identity.key, _json(outcome), digest),
        )
        decision = {
            "profile": "owa/external-action-recovery/v1",
            "action_key": identity.key,
            "admission_digest": admission_digest,
            "outcome_digest": digest,
        }
        policy = EffectReceipt(
            effect_request_digest(RECOVERY_OPERATION, decision),
            "external-recovery",
            status="completed",
            output=decision,
        )
        insert_completed_receipt(
            connection, replace(identity, operation=RECOVERY_OPERATION), policy
        )
        resolved = replace(
            recorded,
            status=outcome["status"],
            output=outcome["output"],
            error=outcome["error"],
            error_status_code=outcome["error_status_code"],
            external_outcome=ExternalOutcomeDependency(admission_digest, digest),
        )
        connection.execute(
            "UPDATE workflow_effects SET status=?,payload=? "
            "WHERE identity=? AND owner=? AND status='pending'",
            (resolved.status, _json(asdict(resolved)), identity.key, recorded.owner),
        )
        verify_external_receipt(connection, journal._invocations, identity, resolved)
        return resolved, policy
