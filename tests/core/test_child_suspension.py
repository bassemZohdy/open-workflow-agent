"""CH05 transport preparation does not qualify runtime child-wait semantics."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import WorkflowExecutionError

PARENT = "0869a9f3-a82a-4cbe-a71d-74e23856c284"
CHILD = "d9a8b234-bb2e-4339-82fd-8f028408ce40"
REQUEST = "a" * 64


def _identity():
    return EffectIdentity(PARENT, "b" * 64, "/do/0/child", (("/do", 1),), "run:workflow")


@pytest.mark.parametrize("deadline", [None, 1060, 1060.25])
def test_child_signal_preserves_exact_transport_and_parent_identity(deadline):
    identity = _identity()
    signal = ChildSuspension(identity, CHILD, REQUEST, deadline_at=deadline)
    expected = {
        "profile": "owa/child-suspension/v1",
        "identity": {
            "invocation_id": PARENT,
            "workflow_fingerprint": "b" * 64,
            "task_reference": "/do/0/child",
            "occurrence": [["/do", 1]],
            "operation": "run:workflow",
        },
        "child_id": CHILD,
        "request_digest": REQUEST,
        "deadline_at": float(deadline) if deadline is not None else None,
    }
    assert signal.as_value() == expected
    restored = ChildSuspension.from_value(json.loads(json.dumps(expected, allow_nan=False)))
    assert restored.as_value() == expected
    assert restored.identity == identity and restored.identity is not identity
    assert restored.child_id != restored.identity.invocation_id
    assert signal.args == (identity.key,)


@pytest.mark.parametrize(
    "field", ["identity", "child_id", "request_digest", "deadline_at", "_evidence"]
)
def test_child_signal_evidence_cannot_be_replaced_or_removed(field):
    signal = ChildSuspension(_identity(), CHILD, REQUEST)
    with pytest.raises(AttributeError, match="immutable"):
        setattr(signal, field, None)
    with pytest.raises(AttributeError, match="immutable"):
        delattr(signal, field)
    with pytest.raises(FrozenInstanceError):
        signal.identity.operation = "call:http"


def test_transport_mutation_cannot_change_retained_or_restored_evidence():
    signal = ChildSuspension(_identity(), CHILD, REQUEST, deadline_at=1060)
    original = signal.as_value()
    source = signal.as_value()
    restored = ChildSuspension.from_value(source)
    source["identity"]["occurrence"][0][1] = 99
    source["identity"]["invocation_id"] = CHILD
    source["child_id"] = PARENT
    source["request_digest"] = "c" * 64
    source["deadline_at"] = 9000
    assert signal.as_value() == original
    assert restored.as_value() == original


@pytest.mark.parametrize(
    "field", ["profile", "identity", "child_id", "request_digest", "deadline_at"]
)
def test_missing_transport_field_is_rejected(field):
    value = ChildSuspension(_identity(), CHILD, REQUEST).as_value()
    del value[field]
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension.from_value(value)


@pytest.mark.parametrize(
    "damage", [None, [], "signal", 1, True, {"profile": "owa/timer-suspension/v1"}]
)
def test_wrong_transport_shape_is_rejected(damage):
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension.from_value(damage)


@pytest.mark.parametrize(
    "field,value",
    [
        ("extra", True),
        ("profile", "owa/timer-suspension/v1"),
        ("identity", None),
        ("identity", []),
        ("child_id", None),
        ("child_id", True),
        ("child_id", UUID(CHILD)),
        ("child_id", ""),
        ("child_id", "child-from-another-quota-tree"),
        ("child_id", CHILD.upper()),
        ("child_id", UUID(CHILD).hex),
        ("child_id", PARENT),
        ("request_digest", None),
        ("request_digest", ""),
        ("request_digest", "a" * 63),
        ("request_digest", "A" * 64),
        ("request_digest", "g" * 64),
    ],
)
def test_extra_or_malformed_transport_values_are_rejected(field, value):
    transport = ChildSuspension(_identity(), CHILD, REQUEST).as_value()
    transport[field] = value
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension.from_value(transport)


@pytest.mark.parametrize(
    "field,value",
    [
        ("extra", "unknown"),
        ("invocation_id", ""),
        ("workflow_fingerprint", None),
        ("task_reference", []),
        ("operation", "wait:arm:v1"),
        ("operation", "policy:detached-child:v1"),
        ("occurrence", []),
        ("occurrence", [["/do", 0]]),
        ("occurrence", [["/do", True]]),
        ("occurrence", [["/do", 1, 2]]),
    ],
)
def test_malformed_or_non_child_effect_identity_is_rejected(field, value):
    transport = ChildSuspension(_identity(), CHILD, REQUEST).as_value()
    transport["identity"][field] = value
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension.from_value(transport)


@pytest.mark.parametrize(
    "deadline",
    [True, False, 0, -1, "1060", [], float("inf"), float("-inf"), float("nan"), 10**1000],
)
def test_invalid_original_deadline_is_rejected_without_silent_coercion(deadline):
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension(_identity(), CHILD, REQUEST, deadline_at=deadline)
    value = ChildSuspension(_identity(), CHILD, REQUEST).as_value()
    value["deadline_at"] = deadline
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension.from_value(value)


def test_direct_constructor_rejects_untyped_or_mutable_identity():
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension({}, CHILD, REQUEST)
    mutable = replace(_identity(), occurrence=[["/do", 1]])
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension(mutable, CHILD, REQUEST)
    nested_mutable = replace(_identity(), occurrence=(["/do", 1],))
    with pytest.raises(WorkflowExecutionError, match="child suspension evidence"):
        ChildSuspension(nested_mutable, CHILD, REQUEST)


def test_private_control_flow_escapes_exception_handlers_and_context_unwinding():
    signal = ChildSuspension(_identity(), CHILD, REQUEST)
    escaped = False
    ordinary_fault = False

    @contextmanager
    def owned_execution():
        try:
            yield
        finally:
            # Match contextlib's legitimate exception traceback management.
            signal.__traceback__ = signal.__traceback__

    try:
        with owned_execution():
            try:
                raise signal
            except Exception:
                ordinary_fault = True
    except ChildSuspension as caught:
        assert caught is signal
        escaped = True
    assert escaped and not ordinary_fault
    assert isinstance(signal, BaseException) and not isinstance(signal, Exception)
