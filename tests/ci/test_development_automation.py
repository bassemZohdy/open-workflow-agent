"""Development pauses preserve manual validation and fail-closed publication."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


def test_every_validation_job_requires_manual_dispatch_or_explicit_ci_opt_in():
    expected = "github.event_name == 'workflow_dispatch' || vars.OWA_AUTOMATIC_CI == 'true'"
    for path in WORKFLOWS.glob("*.yml"):
        if path.name == "release.yml":
            continue
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert "workflow_dispatch" in workflow["on"], path.name
        for name, job in workflow["jobs"].items():
            condition = job.get("if", "")
            assert condition == expected or condition.startswith(f"({expected}) && ("), (
                path.name,
                name,
            )


def test_publication_opt_in_preserves_successful_main_push_requirement():
    workflow = yaml.load(
        (WORKFLOWS / "release.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    condition = workflow["jobs"]["prepare"]["if"]
    assert condition.startswith("vars.OWA_AUTOMATIC_RELEASES == 'true' &&")
    assert "vars.OWA_AUTOMATIC_CI == 'true' &&" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event.workflow_run.event == 'push'" in condition
    assert "github.event.workflow_run.head_branch == 'main'" in condition
    assert "workflow_dispatch" not in workflow["on"]
