from __future__ import annotations

from io import StringIO
from pathlib import Path

from open_workflow_agent import cli
from open_workflow_agent.errors import WorkflowSchemaError


def test_validate_loads_then_compiles_without_exposing_the_plan(
    monkeypatch, tmp_path: Path
) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text("document: {}\n", encoding="utf-8")
    loaded = {"document": {"name": "example"}}
    internal_plan = object()
    calls: list[object] = []

    def load_workflow(path: Path) -> dict[str, object]:
        assert path == workflow_path
        calls.append("load")
        return loaded

    def compile_workflow(workflow: dict[str, object]) -> object:
        assert workflow is loaded
        calls.append("compile")
        return internal_plan

    monkeypatch.setattr(cli, "load_workflow", load_workflow)
    monkeypatch.setattr(cli, "compile_workflow", compile_workflow)
    stdout = StringIO()
    stderr = StringIO()

    result = cli.main(["validate", str(workflow_path)], stdout=stdout, stderr=stderr)

    assert result == 0
    assert calls == ["load", "compile"]
    assert "valid for the default OWA portable profile" in stdout.getvalue()
    assert repr(internal_plan) not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_validate_reports_safe_common_error_without_details(monkeypatch, tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"

    def fail_load(_path: Path) -> dict[str, object]:
        raise WorkflowSchemaError(
            "invalid workflow YAML", details={"secret": "must-not-be-printed"}
        )

    monkeypatch.setattr(cli, "load_workflow", fail_load)
    stdout = StringIO()
    stderr = StringIO()

    result = cli.main(["validate", str(workflow_path)], stdout=stdout, stderr=stderr)

    assert result == 1
    assert stdout.getvalue() == ""
    assert "workflow_schema_error: invalid workflow YAML" in stderr.getvalue()
    assert "must-not-be-printed" not in stderr.getvalue()


def test_validate_reports_invalid_utf8_without_echoing_path(tmp_path: Path) -> None:
    workflow_path = tmp_path / "secret-location" / "workflow.yaml"
    workflow_path.parent.mkdir()
    workflow_path.write_bytes(b"document: \xff\n")
    stdout = StringIO()
    stderr = StringIO()

    result = cli.main(["validate", str(workflow_path)], stdout=stdout, stderr=stderr)

    assert result == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "workflow_schema_error: workflow source must be valid UTF-8\n"
    assert str(workflow_path) not in stderr.getvalue()


def test_validate_real_official_schema_and_compiler_without_returning_private_data(tmp_path):
    workflow_path = tmp_path / "real-workflow.yaml"
    workflow_path.write_text(
        "document:\n  dsl: '1.0.3'\n  namespace: cli\n  name: actual-admission\n"
        "  version: '1.0.0'\ndo:\n  - assign:\n      set:\n"
        "        private: private-plan-sentinel\n",
        encoding="utf-8",
    )
    stdout, stderr = StringIO(), StringIO()
    assert cli.main(["validate", str(workflow_path)], stdout=stdout, stderr=stderr) == 0
    assert "valid for the default OWA portable profile" in stdout.getvalue()
    assert "private-plan-sentinel" not in stdout.getvalue()
    assert stderr.getvalue() == ""
