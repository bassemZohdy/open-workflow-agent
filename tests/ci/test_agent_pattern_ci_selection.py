"""Keep staged pattern source in existing required platform/database gates."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CORE = ("test_agent_roles.py", "test_agent_role_boundaries.py")
CONTRACT = ("test_agent_patterns.py", "test_agent_pattern_process_recovery.py")


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def command(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_patterns_use_existing_windows_native_gate():
    job = workflow("ci.yml")["jobs"]["windows-native-timing"]
    assert job["runs-on"] == "windows-latest"
    text = command(job)
    assert all(f"../../tests/core/{name}" in text for name in CORE)
    assert all(f"../../tests/contract/{name}" in text for name in CONTRACT)


def test_patterns_and_roles_gate_actual_postgresql_images():
    jobs = workflow("postgres-ci.yml")["jobs"]
    assert all(f"tests/core/{name}" in command(jobs["postgres-core"]) for name in CORE)
    for engine in ("adk", "langgraph"):
        key = f"postgres-{engine}-operations"
        job = jobs[key]
        assert key in jobs["postgres-runtime"]["needs"]
        assert job["services"]["postgres"]["image"] == "postgres:16"
        text = command(job)
        assert f"--directory engines/{engine}" in text and "-k postgresql" in text
        assert all(f"../../tests/contract/{name}" in text for name in CONTRACT)
    paths = workflow("postgres-ci.yml")["on"]["pull_request"]["paths"]
    assert all(
        value in paths
        for value in (
            "core/src/open_workflow_agent/agent_roles.py",
            "resources/patterns/**",
            "tests/core/test_agent_role*.py",
            "tests/contract/*agent_pattern*.py",
        )
    )
