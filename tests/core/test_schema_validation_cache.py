"""Successful official-schema memoization never substitutes admission or trust."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from open_workflow_agent import workflow
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


@pytest.fixture(autouse=True)
def isolated_success_cache():
    workflow._SCHEMA_VALIDATION_SUCCESSES.clear()
    yield
    workflow._SCHEMA_VALIDATION_SUCCESSES.clear()


def source() -> dict[str, Any]:
    value = workflow.generate_default_workflow()
    value["do"] = [{"finish": {"set": {"value": True}}}]
    return value


def count_validations(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    class CountingValidator:
        def __init__(self, schema: Any, **options: Any) -> None:
            self.validator = Draft202012Validator(schema, **options)

        def iter_errors(self, value: Any):
            calls.append(value)
            return self.validator.iter_errors(value)

    monkeypatch.setattr(workflow, "Draft202012Validator", CountingValidator)
    return calls


def test_first_validation_uses_original_source_then_equal_json_reuses_success(monkeypatch):
    calls = count_validations(monkeypatch)
    value = source()
    workflow.validate_schema(value)
    reordered = dict(reversed(list(copy.deepcopy(value).items())))
    workflow.validate_schema(reordered)
    assert len(calls) == 1 and calls[0] is value
    assert len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 1


def test_changed_invalid_source_is_revalidated_and_retains_original_error(monkeypatch):
    calls = count_validations(monkeypatch)
    value = source()
    workflow.validate_schema(value)
    value["document"]["version"] = "not-a-version"
    messages = []
    for _ in range(2):
        with pytest.raises(WorkflowSchemaError) as error:
            workflow.validate_schema(value)
        messages.append(str(error.value))
    assert len(calls) == 3 and messages[0] == messages[1]
    assert len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 1


@pytest.mark.parametrize("invalid", [(1,), {1: "value"}, float("nan"), float("inf")])
def test_non_json_cannot_collide_with_a_successful_json_record(monkeypatch, invalid):
    calls = count_validations(monkeypatch)
    value = source()
    workflow.validate_schema(value)
    value["do"][0]["finish"]["set"]["value"] = invalid
    for _ in range(2):
        with pytest.raises(WorkflowSchemaError):
            workflow.validate_schema(value)
    assert len(calls) == 1 and len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 1


def test_unusual_mapping_is_validated_fresh_without_python_json_coercion(monkeypatch):
    calls = count_validations(monkeypatch)

    class DerivedDictionary(dict):
        pass

    value = DerivedDictionary(source())
    workflow.validate_schema(value)
    workflow.validate_schema(value)
    assert len(calls) == 2 and not workflow._SCHEMA_VALIDATION_SUCCESSES


def test_boolean_integer_and_changed_values_have_distinct_validation_keys(monkeypatch):
    calls = count_validations(monkeypatch)
    value = source()
    workflow.validate_schema(value)
    value["do"][0]["finish"]["set"]["value"] = 1
    workflow.validate_schema(value)
    value["do"][0]["finish"]["set"]["value"] = 1.0
    workflow.validate_schema(value)
    assert len(calls) == len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 3


def test_official_schema_mutation_and_validator_replacement_force_fresh_checks(monkeypatch):
    calls = count_validations(monkeypatch)
    schema = copy.deepcopy(workflow._official_schema())
    monkeypatch.setattr(workflow, "_official_schema", lambda: schema)
    value = source()
    workflow.validate_schema(value)
    schema["not"] = {}
    with pytest.raises(WorkflowSchemaError):
        workflow.validate_schema(value)
    schema.pop("not")
    workflow.validate_schema(value)
    assert len(calls) == 2
    replacement_calls = count_validations(monkeypatch)
    workflow.validate_schema(value)
    assert len(replacement_calls) == 1


def test_success_cache_evicts_oldest_and_never_retains_oversized_sources(monkeypatch):
    calls = count_validations(monkeypatch)
    for number in range(33):
        value = source()
        value["document"]["name"] = f"cache-{number}"
        workflow.validate_schema(value)
    assert len(calls) == 33 and len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 32
    oldest = source()
    oldest["document"]["name"] = "cache-0"
    workflow.validate_schema(oldest)
    assert len(calls) == 34 and len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 32
    oversized = source()
    oversized["do"][0]["finish"]["set"]["value"] = "x" * 1_048_576
    before = list(workflow._SCHEMA_VALIDATION_SUCCESSES)
    workflow.validate_schema(oversized)
    workflow.validate_schema(oversized)
    assert len(calls) == 36 and list(workflow._SCHEMA_VALIDATION_SUCCESSES) == before


def test_schema_success_never_skips_current_capability_admission():
    value = source()
    value["do"] = [{"unsafe": {"run": {"shell": {"command": "echo ignored"}}}}]
    for _ in range(2):
        with pytest.raises(UnsupportedWorkflowFeature):
            workflow.compile_workflow(value)
    assert len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 1


def test_custom_descriptor_registry_is_fresh_even_after_official_schema_hit(monkeypatch):
    calls = count_validations(monkeypatch)
    uri = "https://schemas.test/current"
    value = source()
    spec = {"schema": {"document": {"$ref": uri}}}
    value["input"] = spec
    workflow.validate_schema(value)
    workflow.validate_schema(copy.deepcopy(value))
    executor = workflow.WorkflowExecutor(None)
    state = workflow.ExecutionState(data=1)
    for expected_type, valid in (("integer", True), ("string", False), ("integer", True)):
        state.metadata["_schema_registry"] = Registry().with_resources(
            [
                (
                    uri,
                    Resource.from_contents(
                        {"type": expected_type}, default_specification=DRAFT202012
                    ),
                )
            ]
        )
        if valid:
            executor._validate_data_schema(spec, 1, "task input", state)
        else:
            with pytest.raises(WorkflowSemanticError):
                executor._validate_data_schema(spec, 1, "task input", state)
    assert len(calls) == 4 and len(workflow._SCHEMA_VALIDATION_SUCCESSES) == 1
