"""Candidate-context matching for the embedded event subscription profile."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from .errors import EventValidationError
from .events import EventDecision, EventEnvelope, EventProgress
from .expressions import ExpressionEvaluator, validate_expression_data


def json_equal(left: Any, right: Any) -> bool:
    """JSON equality preserves null and does not equate booleans with numbers."""
    validate_expression_data(left)
    validate_expression_data(right)
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            json_equal(value, right[key]) for key, value in left.items()
        )
    return bool(left == right)


def event_projection(event: EventEnvelope, read: str) -> Any:
    if read == "envelope":
        return event.as_dict()
    if read == "raw":
        return event.raw()
    return copy.deepcopy(event.data)


def candidate_matcher(
    strategy: Mapping[str, Any],
    *,
    expressions: ExpressionEvaluator,
    variables: Mapping[str, Any] | None = None,
    read: str = "data",
    filter_guard: Callable[[Mapping[str, Any], EventEnvelope], bool] | None = None,
) -> Callable[[EventEnvelope, EventProgress], EventDecision | None]:
    """Expectations are already evaluated against registration input by Core.

    Property predicates use the property's value, correlation extraction uses
    the envelope, and until expressions use the consumed event projection.
    The existing jq service bounds JSON and sanitizes native failures. Neither
    this adapter nor input length bounds promise hard CPU preemption.
    """
    captured = copy.deepcopy(dict(strategy))
    bindings = copy.deepcopy(dict(variables or {}))
    validate_expression_data(captured)

    def filters_for(current: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
        for mode in ("one", "all", "any"):
            if mode in current:
                value = current[mode]
                if mode == "one" and isinstance(value, Mapping):
                    return mode, [value]
                if (
                    mode != "one"
                    and isinstance(value, list)
                    and all(isinstance(item, Mapping) for item in value)
                ):
                    return mode, value
        raise EventValidationError("event subscription strategy is invalid")

    def match_filter(
        event: EventEnvelope, selected: Mapping[str, Any], saved: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if filter_guard is not None and not filter_guard(selected, event):
            return None
        values = event.as_dict()
        properties = selected.get("with", {})
        if not isinstance(properties, Mapping):
            raise EventValidationError("event filter properties are invalid")
        predicates: list[tuple[str, str]] = []
        # Persisted JSON canonicalizes key order. Reject static property
        # restrictions before evaluating payload-dependent predicates, so an
        # unrelated arrival cannot fault a listener with a different shape.
        for name, expected in properties.items():
            if name not in values:
                return None
            candidate = values[name]
            if isinstance(expected, str):
                if "${" in expected:
                    predicates.append((name, expected))
                elif not isinstance(candidate, str) or not expressions.condition(
                    "${ test($eventPattern) }",
                    candidate,
                    variables={**bindings, "eventPattern": expected},
                ):
                    return None
            elif not json_equal(candidate, expected):
                return None
        for name, predicate in predicates:
            if not expressions.condition(predicate, values[name], variables=bindings):
                return None
        result: dict[str, Any] = {}
        correlations = selected.get("correlate", {})
        if not isinstance(correlations, Mapping):
            raise EventValidationError("event correlations are invalid")
        for name, rule in correlations.items():
            if not isinstance(rule, Mapping) or not isinstance(rule.get("from"), str):
                raise EventValidationError("event correlation extraction is invalid")
            actual = expressions.evaluate(rule["from"], values, variables=bindings)
            validate_expression_data(actual)
            if name in saved and not json_equal(actual, saved[name]):
                return None
            if "expect" in rule and not json_equal(actual, rule["expect"]):
                return None
            result[name] = actual
        return result

    def decide(
        current: Mapping[str, Any], event: EventEnvelope, progress: EventProgress
    ) -> EventDecision | None:
        mode, filters = filters_for(current)
        until = current.get("until")
        if isinstance(until, Mapping):
            saved_until = progress.progress.get("until", {})
            if not isinstance(saved_until, Mapping):
                raise EventValidationError("event terminator progress is invalid")
            terminator_progress = replace(
                progress,
                correlations=saved_until.get("correlations", {}),
                matched_filters=tuple(saved_until.get("matched_filters", ())),
                progress=saved_until.get("progress", {}),
            )
            terminator = decide(until, event, terminator_progress)
            if terminator is not None:
                updates = {
                    "until": {
                        "correlations": {
                            **terminator_progress.correlations,
                            **terminator.correlations,
                        },
                        "matched_filters": sorted(
                            set(terminator_progress.matched_filters)
                            | set(terminator.filter_indices)
                        ),
                        "progress": dict(terminator.progress),
                    }
                }
                return EventDecision(
                    terminal=terminator.terminal,
                    terminator=True,
                    progress={**progress.progress, **updates},
                )
        matches: list[int] = []
        new_bindings: dict[str, Any] = {}
        for index, selected in enumerate(filters):
            if mode == "all" and index in progress.matched_filters:
                continue
            extracted = match_filter(event, selected, {**progress.correlations, **new_bindings})
            if extracted is not None:
                matches.append(index)
                new_bindings.update(extracted)
        if not matches and not (mode == "any" and not filters):
            return None
        terminal = mode != "all" or len(set(progress.matched_filters) | set(matches)) == len(
            filters
        )
        if until is not None:
            terminal = isinstance(until, str) and expressions.condition(
                until, event_projection(event, read), variables=bindings
            )
            if terminal:
                return EventDecision(terminal=True, terminator=True, progress=progress.progress)
        return EventDecision(
            filter_indices=tuple(matches),
            correlations=new_bindings,
            terminal=terminal,
            progress=progress.progress,
        )

    filters_for(captured)
    return lambda event, progress: decide(captured, event, progress)
