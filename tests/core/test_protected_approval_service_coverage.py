"""Authorization and bounded-scan failure paths for protected approvals."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from open_workflow_agent.approvals import (
    APPROVAL_DECISION_EVENT,
    APPROVAL_REQUEST_EVENT,
    ApprovalEventBus,
    ApprovalStore,
    _parse_event_time,
    _parse_expiry,
)
from open_workflow_agent.durable_events import DurableEventBus
from open_workflow_agent.errors import (
    ApprovalAuthorizationError,
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalValidationError,
    OwaError,
)
from open_workflow_agent.events import EventEnvelope
from open_workflow_agent.protected_approval_service import (
    authorize_actor,
    expire_due,
    mutate_protected,
    visible_record,
)
from open_workflow_agent.security import Principal


def _service(**overrides):
    values = {
        "enabled": True,
        "protected_store": None,
        "actor_security_profiles": (),
        "event_bus": None,
        "security": None,
        "store": SimpleNamespace(connection=SimpleNamespace(is_postgresql=False)),
        "_expiry_cursor": None,
        "_expiry_errors": [],
    }

    def ensure_enabled():
        if not values["enabled"]:
            raise ApprovalAuthorizationError("durable approvals are not enabled")

    values["ensure_enabled"] = ensure_enabled
    values.update(overrides)
    return SimpleNamespace(**values)


def test_actor_authentication_requires_protected_storage_and_string_header() -> None:
    with pytest.raises(ApprovalAuthorizationError, match="authentication failed"):
        authorize_actor(_service(), "Bearer token")

    service = _service(protected_store=object())
    with pytest.raises(ApprovalAuthorizationError, match="authentication failed"):
        authorize_actor(service, None)
    with pytest.raises(ApprovalAuthorizationError, match="authentication failed"):
        authorize_actor(service, "Bearer token", operator_id="someone-else")


@pytest.mark.parametrize(
    "actor",
    [None, "operator-a"],
)
def test_visible_record_rejects_non_principal_or_missing_store(actor) -> None:
    with pytest.raises(ApprovalAuthorizationError, match="outside protected actor authority"):
        visible_record(_service(), "approval-1", actor)


@pytest.mark.asyncio
async def test_mutation_checks_protected_bus_actor_revision_and_expiry_origin() -> None:
    service = _service()
    with pytest.raises(ApprovalAuthorizationError, match="service is unavailable"):
        await mutate_protected(
            service,
            "approval-1",
            action="approved",
            actor=Principal("operator-a"),
            revision=1,
            operation_key="op-1",
        )

    durable_bus = object.__new__(DurableEventBus)
    service = _service(
        protected_store=object(),
        event_bus=object.__new__(ApprovalEventBus),
    )
    service.event_bus.delegate = durable_bus
    with pytest.raises(ApprovalValidationError, match="requires actor, revision"):
        await mutate_protected(
            service,
            "approval-1",
            action="approved",
            actor=object(),
            revision=True,
            operation_key="",
        )

    with pytest.raises(ApprovalAuthorizationError, match="original deadline"):
        await mutate_protected(
            service,
            "approval-1",
            action="expired",
            actor=Principal("system:approval-expiry"),
            revision=1,
            operation_key="expiry:approval-1",
        )


@pytest.mark.asyncio
async def test_expiry_scan_returns_without_store_and_rejects_unbounded_limits() -> None:
    assert await expire_due(_service()) == 0
    service = _service(protected_store=object())
    for limit in (0, 1001, True, 1.5):
        with pytest.raises(ApprovalValidationError, match="scan bound is invalid"):
            await expire_due(service, limit=limit)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _ExpiryConnection:
    is_postgresql = False

    def __init__(self, pages):
        self.pages = list(pages)
        self.parameters = []

    def execute(self, _query, parameters):
        self.parameters.append(parameters)
        return _Rows(self.pages.pop(0))


def _expiry_service(pages, *, cursor=None):
    connection = _ExpiryConnection(pages)
    return (
        _service(
            protected_store=SimpleNamespace(requests="protected_requests"),
            store=SimpleNamespace(connection=connection, _table="approvals"),
            _expiry_cursor=cursor,
        ),
        connection,
    )


@pytest.mark.asyncio
async def test_expiry_scan_wraps_once_and_continues_after_revision_conflicts(monkeypatch) -> None:
    import open_workflow_agent.protected_approval_service as approval_service

    service, connection = _expiry_service(
        [[], [("approval-a", 3, 100.0), ("approval-b", 4, 101.0)]],
        cursor=(90.0, "old"),
    )
    expired = []

    async def expire(_service, approval_id, **fields):
        expired.append((approval_id, fields["revision"], fields["operation_key"]))
        if approval_id == "approval-b":
            raise ApprovalConflict("revision changed")

    monkeypatch.setattr(approval_service, "mutate_protected", expire)
    assert await expire_due(service, limit=2) == 1
    assert expired == [
        ("approval-a", 3, "expiry:approval-a"),
        ("approval-b", 4, "expiry:approval-b"),
    ]
    assert service._expiry_cursor == (101.0, "approval-b")
    assert len(connection.parameters) == 2
    assert service._expiry_errors == []


@pytest.mark.asyncio
async def test_expiry_scan_records_allowed_and_unrecognized_errors(monkeypatch) -> None:
    import open_workflow_agent.protected_approval_service as approval_service

    service, _connection = _expiry_service([[("approval-a", 3, 100.0), ("approval-b", 4, 101.0)]])

    async def expire(_service, approval_id, **_fields):
        if approval_id == "approval-a":
            raise ApprovalAuthorizationError("actor rejected")

        class UnexpectedApprovalError(OwaError):
            code = "unexpected_approval_failure"

        raise UnexpectedApprovalError("unexpected")

    monkeypatch.setattr(approval_service, "mutate_protected", expire)
    assert await expire_due(service) == 0
    assert service._expiry_errors == [
        "approval_authorization_error",
        "approval_expiry_rejected",
    ]


def test_approval_store_rejects_unbound_protected_requests_and_invalid_decisions() -> None:
    store = ApprovalStore()
    try:
        with pytest.raises(ApprovalValidationError, match="reserved request event type"):
            store.create_request(
                EventEnvelope(
                    "wrong-type",
                    "https://requester.test",
                    "example.other",
                    "2026-10-14T12:00:00Z",
                    subject="wrong-type",
                )
            )
        with pytest.raises(ApprovalValidationError, match="subject to equal"):
            store.create_request(
                EventEnvelope(
                    "approval-mismatch",
                    "https://requester.test",
                    APPROVAL_REQUEST_EVENT,
                    "2026-10-14T12:00:00Z",
                    subject="other-id",
                )
            )
        event = EventEnvelope(
            "approval-1",
            "https://requester.test",
            APPROVAL_REQUEST_EVENT,
            "2026-10-14T12:00:00Z",
            subject="approval-1",
            data={"approvalPolicy": {"profile": "owa/protected-approval/v1"}},
        )
        with pytest.raises(ApprovalValidationError, match="owned workflow request"):
            store.create_request(event)
        with pytest.raises(ApprovalValidationError, match="must be approved or rejected"):
            store.decide("approval-1", decision="deferred", operator_id="operator-a")
        with pytest.raises(ApprovalValidationError, match="unsupported approval status"):
            store.list(status="in-progress")
        with pytest.raises(ApprovalValidationError, match="list limit"):
            store.list(limit=1001)
        with pytest.raises(ApprovalValidationError, match="operator identity is required"):
            store.decide("approval-1", decision="approved", operator_id=" ")
        with pytest.raises(ApprovalNotFound, match="approval not found"):
            store.decide("missing", decision="approved", operator_id="operator-a")
        assert store.list() == []
        assert store.list(status="pending") == []
        assert store.status_counts() == {}
        assert store.decision_for_strategy({}) is None
        assert store.decision_for_strategy({"one": {"with": {"type": "example.other"}}}) is None
        with pytest.raises(ApprovalValidationError, match="stable subject"):
            store.decision_for_strategy({"one": {"with": {"type": APPROVAL_DECISION_EVENT}}})
    finally:
        store.close()


def test_approval_timestamp_parsers_distinguish_optional_expiry_and_bad_values() -> None:
    assert _parse_event_time("not-a-timestamp") > 0
    assert _parse_expiry(None) is None
    for value in (1, "not-a-timestamp"):
        with pytest.raises(ApprovalValidationError, match="RFC 3339 string"):
            _parse_expiry(value)


def test_approval_store_lifecycle_replays_decisions_and_filters_status() -> None:
    store = ApprovalStore()
    event = EventEnvelope(
        "approval-lifecycle",
        "https://requester.test",
        APPROVAL_REQUEST_EVENT,
        "2026-10-14T12:00:00Z",
        subject="approval-lifecycle",
        data={"question": "continue?"},
    )
    try:
        created = store.create_request(event)
        assert created.status == "pending"
        assert created.event_scope == "anonymous"
        assert store.get("approval-lifecycle").request_event == event.as_dict()
        assert [record.approval_id for record in store.list()] == ["approval-lifecycle"]
        assert [record.approval_id for record in store.list(status="pending")] == [
            "approval-lifecycle"
        ]

        decided = store.decide(
            "approval-lifecycle",
            decision="approved",
            operator_id="operator-a",
            value={"allowed": True},
            operation_key="approval-lifecycle:decision-1",
        )
        assert decided.status == "approved"
        assert store.status_counts() == {"approved": 1}
        assert (
            store.decide(
                "approval-lifecycle",
                decision="approved",
                operator_id="operator-a",
                value={"allowed": True},
                operation_key="approval-lifecycle:decision-1",
            ).status
            == "approved"
        )
        with pytest.raises(ApprovalConflict, match="already has a terminal decision"):
            store.decide(
                "approval-lifecycle",
                decision="rejected",
                operator_id="operator-b",
                operation_key="approval-lifecycle:decision-2",
            )
        replayed = store.decision_for_strategy(
            {
                "one": {
                    "with": {
                        "type": APPROVAL_DECISION_EVENT,
                        "subject": "approval-lifecycle",
                    }
                }
            }
        )
        assert replayed is not None
        assert replayed.data["decision"] == "approved"
    finally:
        store.close()
