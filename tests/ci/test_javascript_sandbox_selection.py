"""Optional JavaScript has mandatory physical/native platform qualification."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def require_node(job):
    assert job["env"]["OWA_TEST_REQUIRED_JAVASCRIPT"] == "1"
    step = next(step for step in job["steps"] if step.get("uses") == "actions/setup-node@v7")
    assert step["with"]["node-version"] == "24"


def command(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_javascript_physical_and_both_native_windows_sqlite_profiles_are_required():
    jobs = workflow("ci.yml")["jobs"]
    for name in ("root", "engines", "windows-operation-adapters", "windows-grpc-sqlite-matrix"):
        require_node(jobs[name])
    assert "../../tests/core/test_javascript_sandbox.py" in command(
        jobs["windows-operation-adapters"]
    )
    native = jobs["windows-grpc-sqlite-matrix"]
    assert {row["engine"] for row in native["strategy"]["matrix"]["include"]} == {
        "adk",
        "langgraph",
    }
    assert "../../tests/contract/test_javascript_sandbox_contract.py" in command(native)
    assert "-k sqlite" in command(native)
    assert jobs["engines"]["env"]["OWA_TEST_JAVASCRIPT_ENGINE"] == "${{ matrix.engine }}"


def test_javascript_actual_postgresql_profiles_gate_runtime_images():
    jobs = workflow("postgres-ci.yml")["jobs"]
    for engine in ("adk", "langgraph"):
        name = f"postgres-{engine}-operations"
        job = jobs[name]
        require_node(job)
        assert job["env"]["OWA_TEST_JAVASCRIPT_ENGINE"] == engine
        assert job["services"]["postgres"]["image"] == "postgres:16"
        assert name in jobs["postgres-runtime"]["needs"]
        assert "../../tests/contract/test_javascript_sandbox_contract.py" in command(job)
        assert "-k postgresql" in command(job)


def test_javascript_packaged_runtime_exercises_a_real_interactive_container():
    job = workflow("ci.yml")["jobs"]["containers"]
    assert "bash tests/ci/javascript_sandbox_image_acceptance.sh" in command(job)
    script = (ROOT / "tests/ci/javascript_sandbox_image_acceptance.sh").read_text()
    assert "--interactive" in script and "--network none" in script
    assert 'result.status == "completed"' in script
    for engine in ("adk", "langgraph"):
        dockerfile = (ROOT / f"docker/Dockerfile.{engine}").read_text()
        assert "node:24-bookworm-slim@sha256:" in dockerfile
        assert "COPY --from=javascript /usr/local/bin/node /usr/local/bin/node" in dockerfile


def test_validation_workflows_have_no_duplicate_mapping_keys():
    def check(node):
        if isinstance(node, yaml.MappingNode):
            names = [key.value for key, _ in node.value]
            assert len(names) == len(set(names)), names
            for _, value in node.value:
                check(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                check(value)

    for path in (ROOT / ".github/workflows").glob("*.yml"):
        check(yaml.compose(path.read_text(), Loader=yaml.BaseLoader))
