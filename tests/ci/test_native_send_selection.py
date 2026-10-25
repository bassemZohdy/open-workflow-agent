"""Native Send needs actual SDK platform and durable-store recovery cells."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SELECTOR = "../../tests/langgraph/test_native_send.py"


def test_native_send_windows_and_postgresql_selections_remain_required():
    for filename, job_name in (
        ("ci.yml", "windows-native"),
        ("postgres-ci.yml", "postgres-langgraph"),
    ):
        workflow = yaml.load(
            (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
        )
        job = workflow["jobs"][job_name]
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        assert SELECTOR in commands
        if filename == "postgres-ci.yml":
            assert (
                "-k 'postgresql and (langgraph or completed_native_scope_dependencies)'" in commands
            )
            assert "--extra postgres" in commands
            assert "tests/langgraph/test_native_send.py" in workflow["on"]["pull_request"]["paths"]
            assert job["services"]["postgres"]["image"] == "postgres:16"
        else:
            assert job["runs-on"] == "windows-latest" and "--extra sqlite" in commands
