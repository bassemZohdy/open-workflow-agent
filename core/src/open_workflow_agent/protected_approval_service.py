"""Authenticated common approval operations; no framework/native execution."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

from .durable_events import DurableEventBus
from .errors import ApprovalAuthorizationError, ApprovalConflict, ApprovalValidationError, OwaError
from .protected_approval_policy import ProtectedApprovalPolicy, actor_identity, bounded_json
from .security import (
    BearerSecurityProfile,
    Principal,
    parse_static_credentials,
    resolve_secret,
    static_principal,
)

if TYPE_CHECKING:
    from .approvals import ApprovalRecord, ApprovalService


def authorize_actor(
    service: ApprovalService, authorization: str | None, operator_id: str | None = None
) -> Principal:
    service.ensure_enabled()
    if service.protected_store is None:
        raise ApprovalAuthorizationError("protected approval actor authentication failed")
    matches = []
    try:
        if not isinstance(authorization, str):
            raise ValueError
        raw_headers = [(b"authorization", authorization.encode("ascii"))]
        for name in service.actor_security_profiles:
            profile = service.security.profile(name)
            if not isinstance(profile, BearerSecurityProfile) or not profile.principal:
                raise ValueError
            supplied = parse_static_credentials(raw_headers, profile)
            expected = resolve_secret(profile.token)
            if len(expected.encode("utf-8")) > 8192:
                raise ValueError
            if hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                actor_identity(profile.principal)
                matches.append(static_principal(profile))
    except (OwaError, ValueError, TypeError, UnicodeError, ApprovalValidationError):
        raise ApprovalAuthorizationError("protected approval actor authentication failed") from None
    if len(matches) != 1 or operator_id is not None and operator_id != matches[0].identity:
        raise ApprovalAuthorizationError("protected approval actor authentication failed")
    return matches[0]


def visible_record(service: ApprovalService, approval_id: str, actor: Principal) -> ApprovalRecord:
    from .errors import ApprovalNotFound

    if not isinstance(actor, Principal) or service.protected_store is None:
        raise ApprovalAuthorizationError("approval is outside protected actor authority")
    if (
        service.protected_store.connection.execute(
            f"SELECT approval_id FROM {service.protected_store.requests} WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        is None
    ):
        raise ApprovalNotFound("approval not found")
    request, _ = service.protected_store.inspect(approval_id)
    policy = ProtectedApprovalPolicy.from_value(request["policy"])
    if actor.identity not in {
        policy.assignee,
        *policy.reassignment_targets,
        *policy.escalation_targets,
    }:
        raise ApprovalAuthorizationError("approval is outside protected actor authority")
    record = service.store.get(approval_id)
    if record is None:
        raise ApprovalNotFound("approval not found")
    return record


async def mutate_protected(
    service: ApprovalService,
    approval_id: str,
    *,
    action: str,
    actor: Principal,
    revision: int | None,
    operation_key: str | None,
    value: Any = None,
    assignee: str | None = None,
    original_expiry: bool = False,
) -> ApprovalRecord:
    from .approvals import ApprovalEventBus

    service.ensure_enabled()
    bus = service.event_bus
    if (
        service.protected_store is None
        or not isinstance(bus, ApprovalEventBus)
        or not isinstance(bus.delegate, DurableEventBus)
    ):
        raise ApprovalAuthorizationError("protected approval service is unavailable")
    if not isinstance(actor, Principal) or type(revision) is not int or not operation_key:
        raise ApprovalValidationError(
            "protected approval requires actor, revision and idempotency key"
        )
    if action == "expired" and not original_expiry:
        raise ApprovalAuthorizationError("approval expiry is controlled by its original deadline")
    bounded_json(value)
    with bus.delegate.store.transaction():
        request, _ = service.protected_store.inspect(approval_id)
        event = service.protected_store.mutate(
            approval_id,
            action=action,
            actor=actor.identity,
            revision=revision,
            operation_key=operation_key,
            value=value,
            assignee=assignee,
        )
        if event is not None:
            bus.delegate.store.ingest_in_transaction(
                event.as_dict(),
                default_source=event.source,
                scope=request["event_scope"],
            )
    updated = service.store.get(approval_id)
    assert updated is not None
    return updated


async def expire_due(service: ApprovalService, *, limit: int = 100) -> int:
    if service.protected_store is None:
        return 0
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ApprovalValidationError("approval expiry scan bound is invalid")
    clock = (
        "EXTRACT(EPOCH FROM clock_timestamp())"
        if service.store.connection.is_postgresql
        else "((julianday('now')-2440587.5)*86400)"
    )

    protected_store = service.protected_store

    def page(cursor: tuple[float, str] | None) -> Any:
        later = " AND (a.expires_at>? OR (a.expires_at=? AND a.approval_id>?))" if cursor else ""
        parameters = (cursor[0], cursor[0], cursor[1], limit) if cursor else (limit,)
        return service.store.connection.execute(
            "SELECT r.approval_id,r.revision,a.expires_at "
            f"FROM {protected_store.requests} r "
            f"JOIN {service.store._table} a ON a.approval_id=r.approval_id "
            f"WHERE a.status='pending' AND a.expires_at<={clock}{later} "
            "ORDER BY a.expires_at,a.approval_id LIMIT ?",
            parameters,
        ).fetchall()

    rows = page(service._expiry_cursor)
    if not rows and service._expiry_cursor is not None:
        service._expiry_cursor = None
        rows = page(None)  # At most one bounded wrap; no unbounded poison-page retry.
    expired = 0
    for approval_id, revision, expires_at in rows:
        service._expiry_cursor = (float(expires_at), approval_id)
        try:
            await mutate_protected(
                service,
                approval_id,
                action="expired",
                actor=Principal("system:approval-expiry"),
                revision=revision,
                operation_key="expiry:" + approval_id,
                original_expiry=True,
            )
        except ApprovalConflict:
            continue
        except OwaError as error:
            allowed = {
                "approval_authorization_error",
                "approval_validation_error",
                "configuration_error",
                "workflow_execution_error",
                "event_validation_error",
            }
            service._expiry_errors.append(
                error.code if error.code in allowed else "approval_expiry_rejected"
            )
            continue  # The candidate transaction rolled back; retain evidence unchanged.
        expired += 1
    return expired
