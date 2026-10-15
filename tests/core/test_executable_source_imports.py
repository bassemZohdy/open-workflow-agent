"""Imported captures cannot disguise source scripts as private inline bodies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from open_workflow_agent import recovery_definitions
from open_workflow_agent.errors import OwaError, UnsupportedWorkflowFeature
from open_workflow_agent.executable_source_imports import reject_imported_script_sources
from open_workflow_agent.external_catalog import (
    ResolvedCatalogFunction,
    _validate_function_definition,
    parse_catalog_function_reference,
)
from open_workflow_agent.workflow import normalize_workflow

URI = "https://approved.test/source.py"
REFERENCE = "captured:1.0.0@trusted"


def source_task():
    return {"run": {"script": {"language": "python", "source": {"endpoint": URI}}}}


def captured(original, execution=None):
    raw = json.dumps(original, sort_keys=True, separators=(",", ":"))
    return ResolvedCatalogFunction(
        REFERENCE,
        "https://catalog.test/function.yaml",
        hashlib.sha256(raw.encode()).hexdigest(),
        raw,
        None if execution is None else json.dumps(execution, sort_keys=True, separators=(",", ":")),
    )


@pytest.mark.parametrize("position", ["direct", "do", "try", "catch", "fork", "foreach"])
@pytest.mark.parametrize("lane", ["original", "derived"])
def test_imported_source_rejects_original_or_derived_nested_task_before_resolution(position, lane):
    script = source_task()
    if position == "direct":
        task = script
    elif position in {"do", "try"}:
        task = {position: [{"script": script}]}
    elif position == "catch":
        task = {"try": [], "catch": {"do": [{"script": script}]}}
    elif position == "fork":
        task = {"fork": {"branches": [{"branch": script}]}}
    else:
        task = {"listen": {}, "foreach": {"do": [{"script": script}]}}
    inline = {"run": {"script": {"language": "python", "code": "trusted-looking replacement"}}}
    imported = captured(task, inline) if lane == "original" else captured(inline, task)
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        reject_imported_script_sources((imported,))
    assert failure.value.details == {"reason": "imported_executable_source_unsupported"}
    assert "trusted-looking replacement" not in json.dumps(failure.value.as_dict())
    assert imported.reference == REFERENCE


@pytest.mark.parametrize(
    "definition",
    [
        {
            "call": "http",
            "with": {
                "method": "post",
                "endpoint": "https://business.test",
                "body": {"source": URI, "run": {"script": {"source": URI}}},
            },
        },
        {"call": "openapi", "with": {"document": {"endpoint": URI}, "operationId": "read"}},
        {
            "run": {
                "script": {
                    "language": "python",
                    "code": "existing inline boundary is separately rejected",
                }
            }
        },
    ],
)
def test_guard_does_not_confuse_business_payload_or_unrelated_inline_profile_with_source(
    definition,
):
    imported = captured(definition)
    reject_imported_script_sources((imported,))
    assert imported.definition == definition


def test_existing_catalog_admission_rejects_external_scripts_even_with_declared_literal_source():
    reference = parse_catalog_function_reference(REFERENCE)
    assert reference is not None
    with pytest.raises(UnsupportedWorkflowFeature, match="external catalog script functions"):
        _validate_function_definition(source_task(), reference)


@pytest.mark.parametrize("value", [None, {}, "not-a-capture"])
def test_invalid_private_capture_cannot_bypass_guard(value):
    with pytest.raises(UnsupportedWorkflowFeature):
        reject_imported_script_sources((value,))


@pytest.mark.parametrize("lane", ["original", "derived"])
def test_corrupt_private_definition_rejects_without_exposing_captured_code(lane):
    imported = captured({"call": "http", "with": {"endpoint": "https://business.test"}})
    damaged = ResolvedCatalogFunction(
        imported.reference,
        imported.resource_uri,
        imported.digest,
        "private-source-contents" if lane == "original" else imported.definition_json,
        "private-source-contents" if lane == "derived" else None,
    )
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        reject_imported_script_sources((damaged,))
    assert "private-source-contents" not in json.dumps(failure.value.as_dict())


def public_import():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "source-import",
            "name": "original",
            "version": "1.0.0",
        },
        "use": {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}},
        "do": [{"remote": {"call": REFERENCE}}],
    }


@pytest.mark.parametrize("lane", ["original", "derived"])
def test_normalization_rejects_imported_source_before_creating_private_inline_scope(lane):
    inline = {"run": {"script": {"language": "python", "code": "not a replacement source proof"}}}
    imported = (
        captured(source_task(), inline) if lane == "original" else captured(inline, source_task())
    )
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        normalize_workflow(public_import(), imports=(imported,))
    assert failure.value.details["reason"] == "imported_executable_source_unsupported"


def test_restored_archive_cannot_turn_uncaptured_imported_source_into_inline_execution():
    public = public_import()
    admitted_protocol = captured(
        {"call": "http", "with": {"method": "get", "endpoint": "https://business.test"}}
    )
    plan = normalize_workflow(public, imports=(admitted_protocol,))
    disguised = captured(
        source_task(),
        {"run": {"script": {"language": "python", "code": "uncaptured imported bytes"}}},
    )
    changed = replace(plan, imports=(disguised,))
    # Even an internally recomputed fingerprint cannot substitute a qualified
    # source binding for this unsupported catalog profile.
    saved = recovery_definitions._definition(changed)
    with pytest.raises(OwaError):
        recovery_definitions._plan(saved)
