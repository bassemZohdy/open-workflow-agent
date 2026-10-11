import json
from pathlib import Path

from openworkflow_adk.cli import main


def test_cli_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["owf-adk"])
    assert main() == 0
    assert "run" in capsys.readouterr().out


def test_cli_export_emits_pure_openworkflow(tmp_path: Path, monkeypatch, capsys) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        "document:\n"
        "  dsl: '1.0.3'\n"
        "  namespace: demo\n"
        "  name: export-test\n"
        "  version: '1.0.0'\n"
        "do:\n"
        "  - greet:\n"
        "      wait:\n"
        "        seconds: 0\n"
        "      metadata:\n"
        "        adk:\n"
        "          agent:\n"
        "            model: gemini-2.5-flash\n"
        "            instruction: Say hello.\n"
    )
    monkeypatch.setattr("sys.argv", ["owf-adk", "export", str(workflow)])
    assert main() == 0
    output = capsys.readouterr().out
    assert "adk" not in output
    assert "agent" not in output
    assert "document:" in output


def test_cli_lint_strict_rejects_adk_extensions(tmp_path: Path, monkeypatch, capsys) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        "document:\n"
        "  dsl: '1.0.3'\n"
        "  namespace: demo\n"
        "  name: strict-test\n"
        "  version: '1.0.0'\n"
        "do:\n"
        "  - greet:\n"
        "      wait:\n"
        "        seconds: 0\n"
        "      metadata:\n"
        "        adk:\n"
        "          agent:\n"
        "            model: gemini-2.5-flash\n"
        "            instruction: Say hello.\n"
    )
    monkeypatch.setattr("sys.argv", ["owf-adk", "lint", "--strict", str(workflow)])
    assert main() == 1
    output = json.loads(capsys.readouterr().out)
    assert any(item["code"] == "adk-extension" for item in output)
