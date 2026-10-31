"""Finite cross-runtime outcome evidence cannot be promoted by partial overlap."""

from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest
import yaml

from ci.literal_parity import (
    ENGINES,
    MAX_REPORT_BYTES,
    ParityError,
    aggregate,
    envelope,
    expected_records,
    read_report,
    sdk_versions,
    validate_literal_plan,
)

SOURCE = "a" * 40


def test_unicode_fixture_is_read_as_utf8_and_keeps_its_json_scalar():
    record = next(record for record in expected_records() if record["id"] == "literal-object")
    assert record["output"]["values"][2] == "\u96ea"


def test_capture_cli_rejects_a_mislabelled_checkout_without_creating_output(tmp_path):
    output = tmp_path / "outcomes.json"
    result = subprocess.run(
        [
            sys.executable,
            "ci/literal_parity.py",
            "capture",
            "--engine",
            "adk",
            "--source-commit",
            SOURCE,
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2 and b"evidence rejected" in result.stderr
    assert not output.exists()


def test_shared_outcome_job_requires_actual_all_three_source_matched_producers():
    from ci.literal_parity import ROOT

    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    parity = jobs["literal-outcome-parity"]
    assert set(parity["needs"]) == {"javascript-definition-validation", "engines"}
    for name in ("javascript-definition-validation", "engines", "literal-outcome-parity"):
        commands = "\n".join(step.get("run", "") for step in jobs[name]["steps"])
        assert '--source-commit "${{ github.sha }}"' in commands
        uploads = [
            step for step in jobs[name]["steps"] if step.get("uses") == "actions/upload-artifact@v7"
        ]
        assert any(
            step["with"].get("if-no-files-found") == "error"
            and "literal-outcome" in step["with"]["name"]
            for step in uploads
        )
    matrix = jobs["engines"]["strategy"]["matrix"]["include"]
    assert {row["engine"] for row in matrix} == {"adk", "langgraph"}


@pytest.mark.parametrize(
    "body",
    [
        {"call": "llm:1.0.0@default"},
        {"set": {"value": "${ env }"}},
        {"set": {"ok": True}, "output": {"as": "${ env }"}},
    ],
)
def test_capture_admission_rejects_fixture_operations_that_could_leave_literal_profile(body):
    from open_workflow_agent.workflow import compile_workflow

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "tests",
                "name": "capture",
                "version": "1.0.0",
            },
            "do": [{"unsafe": body}],
        }
    )
    with pytest.raises(ParityError):
        validate_literal_plan(plan)


def reports():
    return [
        envelope(
            engine,
            SOURCE,
            sdk_versions()[engine],
            "24.16.0" if engine == "langgraph-js" else "3.12.15",
            expected_records(),
        )
        for engine in sorted(ENGINES)
    ]


def test_complete_synthetic_reports_compare_only_finite_public_outcomes():
    result = aggregate(reports(), SOURCE)
    assert result["case_count"] == 29 and result["engines"] == sorted(ENGINES)
    assert result["outcome_parity"] is True
    assert (
        result["public_identity_parity"]
        is result["durability_parity"]
        is result["full_conformance"]
        is False
    )
    assert "records" not in result


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate_engine",
        "source",
        "sdk",
        "native",
        "fixture",
        "schema",
        "missing_case",
        "duplicate_case",
        "extra_field",
        "outcome",
        "fault_reference",
        "boolean_number",
        "nonfinite",
        "runtime",
    ],
)
def test_mixed_incomplete_or_mismatched_reports_fail_closed_without_raw_artifact_data(damage):
    values = copy.deepcopy(reports())
    if damage == "missing":
        values.pop()
    elif damage == "duplicate_engine":
        values[0] = copy.deepcopy(values[1])
    elif damage == "source":
        values[0]["source_commit"] = "b" * 40
    elif damage == "sdk":
        values[0]["sdk_version"] = "unqualified"
    elif damage == "native":
        values[0]["native"] = False
    elif damage == "fixture":
        values[0]["fixture_sha256"] = {}
    elif damage == "schema":
        values[0]["schema_sha256"] = "b" * 64
    elif damage == "missing_case":
        values[0]["records"].pop()
    elif damage == "duplicate_case":
        values[0]["records"][0] = copy.deepcopy(values[0]["records"][1])
    elif damage == "extra_field":
        values[0]["private"] = "PRIVATE-artifact-payload"
    elif damage == "outcome":
        values[0]["records"][0]["status"] = "waiting"
    elif damage == "fault_reference":
        fault = next(record for record in values[0]["records"] if record["status"] == "faulted")
        fault["error"]["details"]["instance"] = "/wrong/reference"
    elif damage == "boolean_number":
        record = next(record for record in values[0]["records"] if record["id"] == "literal-object")
        record["output"]["values"][1] = True
    elif damage == "nonfinite":
        values[0]["records"][0]["output"] = float("nan")
    else:
        values[0]["runtime_version"] = "PRIVATE-artifact-payload"
    with pytest.raises(ParityError) as caught:
        aggregate(values, SOURCE)
    assert "PRIVATE" not in str(caught.value)


def test_report_reader_rejects_oversized_empty_and_duplicate_json_before_comparison(tmp_path):
    path = tmp_path / "report.json"
    for content in (b"", b"x" * (MAX_REPORT_BYTES + 1), b'{"native":false,"native":true}'):
        path.write_bytes(content)
        with pytest.raises(ParityError):
            read_report(path)


def test_real_cli_persists_only_complete_matched_evidence_and_leaves_output_unchanged_on_failure(
    tmp_path,
):
    directory = tmp_path / "reports"
    directory.mkdir()
    paths = []
    for report in reports():
        path = directory / f"literal-outcomes-{report['engine']}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "parity.json"
    command = [
        sys.executable,
        "ci/literal_parity.py",
        "aggregate",
        "--source-commit",
        SOURCE,
        "--directory",
        str(directory),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    original = output.read_bytes()
    assert json.loads(original)["full_conformance"] is False
    paths[0].unlink()
    result = subprocess.run(command, capture_output=True, timeout=10)
    assert result.returncode == 2 and b"evidence rejected" in result.stderr
    assert output.read_bytes() == original
