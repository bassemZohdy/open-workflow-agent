"""Event candidate predicates must not inspect statically excluded arrivals."""

from __future__ import annotations

import json
from typing import Any

import pytest
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import ExpressionError
from open_workflow_agent.event_matching import candidate_matcher
from open_workflow_agent.events import EventEnvelope, EventProgress, EventRegistration
from open_workflow_agent.expressions import ExpressionEvaluator


def _strategy(order: str) -> dict[str, Any]:
    restrictions = {"type": "^fixture.first$", "subject": "^chosen$"}
    predicate = {"data": "${ .value > 2 }"}
    properties = (
        {**restrictions, **predicate}
        if order == "restrictions-first"
        else {**predicate, **restrictions}
    )
    if order == "canonical-json":
        properties = json.loads(json.dumps(properties, sort_keys=True))
        assert next(iter(properties)) == "data"
    return {
        "one": {
            "with": properties,
            "correlate": {"order": {"from": "${ .data.order }", "expect": "order-1"}},
        }
    }


def _progress(strategy: dict[str, Any]) -> EventProgress:
    identity = EffectIdentity(
        "matcher-invocation", "fingerprint", "/do/0/listen", (("/do/0/listen", 1),), "listen"
    )
    registration = EventRegistration(
        identity, effect_request_digest("listen", strategy), "anonymous"
    )
    return EventProgress(registration)


def _envelope(
    data: Any, *, event_type: str = "fixture.first", subject: str = "chosen"
) -> EventEnvelope:
    return EventEnvelope(
        id="candidate",
        source="urn:matcher-test",
        type=event_type,
        time="2026-10-12T00:00:00Z",
        subject=subject,
        data=data,
        data_present=True,
    )


@pytest.mark.parametrize("order", ["predicate-first", "restrictions-first", "canonical-json"])
@pytest.mark.parametrize("exclusion", ["type", "subject"])
def test_unrelated_scalar_arrival_is_excluded_before_payload_predicate(
    order: str, exclusion: str
) -> None:
    strategy = _strategy(order)
    event = _envelope(
        1,
        event_type="fixture.other" if exclusion == "type" else "fixture.first",
        subject="unrelated" if exclusion == "subject" else "chosen",
    )
    matcher = candidate_matcher(strategy, expressions=ExpressionEvaluator())
    assert matcher(event, _progress(strategy)) is None


@pytest.mark.parametrize("order", ["predicate-first", "restrictions-first", "canonical-json"])
def test_matching_scalar_arrival_preserves_real_expression_failure(order: str) -> None:
    strategy = _strategy(order)
    matcher = candidate_matcher(strategy, expressions=ExpressionEvaluator())
    with pytest.raises(ExpressionError):
        matcher(_envelope(1), _progress(strategy))


@pytest.mark.parametrize("order", ["predicate-first", "restrictions-first", "canonical-json"])
def test_matching_object_arrival_keeps_predicate_and_correlation_results(order: str) -> None:
    strategy = _strategy(order)
    matcher = candidate_matcher(strategy, expressions=ExpressionEvaluator())
    result = matcher(_envelope({"value": 3, "order": "order-1"}), _progress(strategy))
    assert result is not None and result.terminal and result.filter_indices == (0,)
    assert result.correlations == {"order": "order-1"}
    assert matcher(_envelope({"value": 1, "order": "order-1"}), _progress(strategy)) is None


def test_excluded_event_never_evaluates_failing_correlation() -> None:
    strategy = {
        "one": {
            "with": {"type": "^fixture.first$"},
            "correlate": {"order": {"from": '${ error("private correlation") }'}},
        }
    }
    matcher = candidate_matcher(strategy, expressions=ExpressionEvaluator())
    assert matcher(_envelope(1, event_type="fixture.other"), _progress(strategy)) is None
    with pytest.raises(ExpressionError):
        matcher(_envelope(1), _progress(strategy))
