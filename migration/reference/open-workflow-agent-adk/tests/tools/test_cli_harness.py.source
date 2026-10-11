import json

from openworkflow_adk.cli import main


def test_cli_test_harness_runs_json_fixture(tmp_path, monkeypatch, capsys) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """document:
  dsl: '1.0.3'
  namespace: demo
  name: harness
  version: '1.0.0'
do:
  - output:
      set:
        result: '"ok"'
"""
    )
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"name": "basic", "output": "ok"}))
    monkeypatch.setattr("sys.argv", ["owf-adk", "test", str(workflow), "--fixtures", str(cases)])

    assert main() == 0
    assert '"passed": true' in capsys.readouterr().out
