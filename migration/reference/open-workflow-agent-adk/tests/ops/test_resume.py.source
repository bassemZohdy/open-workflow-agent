import asyncio

import pytest

from openworkflow_adk import SQLiteRunHistory, load, run_workflow
from openworkflow_adk.models import TaskItem
from openworkflow_adk.runtime import _resume_task_list


async def test_sqlite_checkpoint_resumes_after_failed_run(tmp_path, monkeypatch) -> None:
    database = tmp_path / "session.db"
    history_path = tmp_path / "history.db"
    monkeypatch.setenv("WORKFLOW_SESSION_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "resume",
                "version": "1.0.0",
            },
            "do": [
                {"prepare": {"set": {"prepared": '"yes"'}}},
                {"work": {"call": "fail"}},
            ],
        }
    )

    def fail() -> None:
        raise RuntimeError("temporary failure")

    history = SQLiteRunHistory(str(history_path))
    with pytest.raises(RuntimeError, match="temporary failure"):
        await asyncio.wait_for(
            run_workflow(
                document,
                session_backend="sqlite",
                session_id="run-1",
                history=history,
                function_registry={"fail": fail},
            ),
            timeout=5,
        )
    history.close()

    restarted_history = SQLiteRunHistory(str(history_path))
    events = await asyncio.wait_for(
        run_workflow(
            document,
            session_backend="sqlite",
            session_id="run-1",
            history=restarted_history,
            function_registry={"fail": lambda: "recovered"},
            resume=True,
        ),
        timeout=5,
    )

    assert events
    assert restarted_history.get("run-1").status == "completed"
    restarted_history.close()


async def test_nested_checkpoint_resumes_inside_branch(tmp_path) -> None:
    """C24.13: checkpoints inside nested do/try bodies resume at the branch."""
    history_path = tmp_path / "nested-history.db"
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "nested-resume",
                "version": "1.0.0",
            },
            "do": [
                {"prepare": {"set": {"prepared": '"yes"'}}},
                {
                    "branch": {
                        "do": [
                            {"first": {"set": {"step": '"one"'}}},
                            {"second": {"call": "fail"}},
                            {"third": {"set": {"step": '"three"'}}},
                        ]
                    }
                },
                {"after": {"set": {"done": '"yes"'}}},
            ],
        }
    )

    def fail() -> None:
        raise RuntimeError("boom")

    history = SQLiteRunHistory(str(history_path))
    with pytest.raises(RuntimeError, match="boom"):
        await run_workflow(
            document,
            session_id="nested-1",
            history=history,
            function_registry={"fail": fail},
        )
    history.close()

    restarted = SQLiteRunHistory(str(history_path))
    events = await run_workflow(
        document,
        session_id="nested-1",
        history=restarted,
        function_registry={"fail": lambda: "recovered"},
        resume=True,
    )
    try:
        assert events
        assert restarted.get("nested-1").status == "completed"
    finally:
        restarted.close()


def test_resume_fork_preserves_unrelated_raw_branch_fields() -> None:
    items = [
        TaskItem.model_validate(
            {
                "race": {
                    "fork": {
                        "branches": [
                            {
                                "first": {
                                    "do": [{"checkpoint": {"wait": {"seconds": 0}}}],
                                    "future_field": None,
                                }
                            },
                            {"second": {"wait": {"seconds": 0}, "future_field": None}},
                        ]
                    }
                }
            }
        )
    ]

    resumed = _resume_task_list(items, "checkpoint")

    assert resumed is not None
    branches = resumed[0].task.fork["branches"]
    assert branches[0]["first"]["future_field"] is None
    assert branches[1]["second"]["future_field"] is None


async def test_run_config_object_drives_execution(tmp_path) -> None:
    """C24.20: the frozen RunConfig path behaves like the keyword path."""
    from openworkflow_adk import RunConfig

    history_path = tmp_path / "config-history.db"
    from openworkflow_adk import SQLiteRunHistory

    history = SQLiteRunHistory(str(history_path))
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "config-run",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"value": '"ok"'}}}],
        }
    )
    config = RunConfig(session_id="config-1", history=history)
    try:
        events = await run_workflow(document, config=config)
        assert events
        assert history.get("config-1").status == "completed"
    finally:
        history.close()
