"""A passing dependency gate must reflect actual nested advisory records."""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path

import pytest
import yaml

from ci.security_audit import AuditReportError, evaluate_report, main


def _report(vulnerabilities=()):
    return {
        "dependencies": [{"name": "urllib3", "version": "2.7.0", "vulns": list(vulnerabilities)}],
        "fixes": [],
    }


def test_nested_advisories_are_found_instead_of_treating_dependencies_as_vulnerabilities():
    report = _report(
        [
            {"id": "PYSEC-2026-4177", "fix_versions": ["2.8.0"]},
            {"id": "PYSEC-2026-4176", "fix_versions": ["2.8.0"]},
            {"id": "PYSEC-2026-4175", "fix_versions": ["2.8.0"]},
        ]
    )
    before = copy.deepcopy(report)
    result = evaluate_report(report, 1)
    assert result.dependencies == 1 and len(result.fixable) == 3 and result.unfixable == ()
    assert result.fixable[0].dependency == "urllib3"
    assert result.fixable[0].version == "2.7.0"
    assert result.fixable[0].fix_versions == ("2.8.0",)
    assert report == before


@pytest.mark.parametrize(
    "vulnerabilities,status,expected",
    [
        ([], 0, 0),
        ([{"id": "GHSA-example", "fix_versions": []}], 1, 0),
        ([{"id": "GHSA-example", "fix_versions": ["2.8.0"]}], 1, 1),
    ],
)
def test_cli_policy_preserves_unfixable_warnings_and_rejects_fixable_advisories(
    tmp_path, capsys, vulnerabilities, status, expected
):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(_report(vulnerabilities)), encoding="utf-8")
    assert main([str(path), "--auditor-status", str(status)]) == expected
    output = capsys.readouterr()
    assert "Audited 1 dependencies" in output.out
    if vulnerabilities:
        assert "urllib3==2.7.0: GHSA-example" in output.out
        assert ("WARNING" in output.out) == (expected == 0)
    assert ("fixable advisories found" in output.err) == (expected == 1)


@pytest.mark.parametrize(
    "report,status",
    [
        ({"urllib3": []}, 0),
        ({"dependencies": [], "fixes": []}, 0),
        ({"dependencies": None, "fixes": []}, 0),
        ({"dependencies": [{"name": "x", "skip_reason": "unavailable"}], "fixes": []}, 1),
        ({"dependencies": [{"name": "x", "version": "1", "vulns": None}], "fixes": []}, 0),
        (_report([{"id": None, "fix_versions": []}]), 1),
        (_report([{"id": "GHSA-x"}]), 1),
        (_report([{"id": "GHSA-x", "fix_versions": "2"}]), 1),
        (_report([{"id": "GHSA-x", "fix_versions": [None]}]), 1),
        (_report([{"id": "GHSA-x", "fix_versions": []}]), 0),
        (_report(), 1),
        (_report(), 2),
        (_report(), True),
        ({**_report(), "fixes": [{"name": "urllib3"}]}, 0),
        (_report([{"id": "GHSA-x", "fix_versions": []}] * 2), 1),
        ({"dependencies": _report()["dependencies"] * 2, "fixes": []}, 0),
        (
            {"dependencies": [{"name": "::error::fake", "version": "1", "vulns": []}], "fixes": []},
            0,
        ),
    ],
)
def test_incomplete_malformed_or_failed_scans_cannot_be_green(report, status):
    with pytest.raises(AuditReportError):
        evaluate_report(report, status)


@pytest.mark.parametrize("content", ["{", '{"dependencies":[],"dependencies":[],"fixes":[]}'])
def test_invalid_or_duplicate_json_fields_fail_without_printing_report(tmp_path, capsys, content):
    path = tmp_path / "audit.json"
    path.write_text(content, encoding="utf-8")
    assert main([str(path), "--auditor-status", "0"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "FAIL:" in output.err and content not in output.err


def test_missing_report_cannot_be_accepted(tmp_path, capsys):
    assert main([str(tmp_path / "missing.json"), "--auditor-status", "0"]) == 1
    assert "FAIL: report could not be read" in capsys.readouterr().err


def test_security_workflow_uses_the_same_checked_gate_and_strict_scanner():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/security.yml").read_text()
    )
    step = next(
        step
        for step in workflow["jobs"]["dependencies"]["steps"]
        if step.get("name") == "Audit resolved dependencies"
    )
    command = step["run"]
    assert "python ci/security_audit.py" in command and '--auditor-status "$status"' in command
    assert "--strict" in command and "--no-deps" in command
    assert "report.items()" not in command and "--ignore-vuln" not in command


def test_security_audits_every_optional_runtime_dependency_in_each_locked_environment():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/security.yml").read_text())
    environments = workflow["jobs"]["dependencies"]["strategy"]["matrix"]["include"]
    for environment in environments:
        project = root / environment["project"]
        metadata = tomllib.loads((project / "pyproject.toml").read_text())
        extras = environment["extras"].split()
        selected = {extras[index + 1] for index, value in enumerate(extras) if value == "--extra"}
        assert selected == set(metadata["project"].get("optional-dependencies", {}))
