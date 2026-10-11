import asyncio

import pytest

from openworkflow_adk import InMemoryRunHistory, SQLiteRunHistory, load, run_workflow
from openworkflow_adk.internal import InMemoryBroker


def _timer_document():
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "timer",
                "version": "1.0.0",
            },
            "do": [
                {"before": {"set": {"before": "true"}}},
                {"pause": {"wait": {"milliseconds": 50}}},
                {"after": {"set": {"after": "true"}}},
            ],
        }
    )


@pytest.mark.parametrize("history_factory", [InMemoryRunHistory])
async def test_long_wait_suspends_without_sleeping(history_factory) -> None:
    history = history_factory()
    document = _timer_document()

    await asyncio.wait_for(
        run_workflow(
            document,
            history=history,
            session_id="timer-run",
            suspend_after=0.01,
        ),
        timeout=0.2,
    )

    record = history.get("timer-run")
    assert record.status == "suspended"
    assert record.checkpoint_task == "pause"
    assert record.state["before"] is True
    assert record.resume_at is not None

    await asyncio.sleep(0.06)
    await run_workflow(document, history=history, session_id="timer-run", resume=True)

    assert history.get("timer-run").status == "completed"
    assert history.get("timer-run").state["after"] is True


async def test_sqlite_suspension_survives_reopen(tmp_path) -> None:
    path = tmp_path / "suspension.db"
    history = SQLiteRunHistory(str(path))
    document = _timer_document()

    await run_workflow(document, history=history, session_id="timer-run", suspend_after=0.01)
    assert history.get("timer-run").status == "suspended"
    history.close()

    await asyncio.sleep(0.06)
    reopened = SQLiteRunHistory(str(path))
    await run_workflow(document, history=reopened, session_id="timer-run", resume=True)

    assert reopened.get("timer-run").status == "completed"
    assert reopened.get("timer-run").state["after"] is True
    reopened.close()


async def test_broker_listen_suspends_and_resumes_on_matching_event() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "listen-suspend",
                "version": "1.0.0",
            },
            "do": [
                {"wait_for_event": {"listen": {"to": {"one": {"with": {"type": "approval"}}}}}},
                {"finish": {"set": {"value": "done"}}},
            ],
        }
    )
    history = InMemoryRunHistory()
    broker = InMemoryBroker()

    await run_workflow(document, history=history, broker=broker, session_id="listen-run")
    assert history.get("listen-run").status == "suspended"
    assert history.get("listen-run").suspension_reason == "broker_listen"

    await broker.publish({"type": "approval", "data": {"approved": True}})
    await run_workflow(
        document,
        history=history,
        broker=broker,
        session_id="listen-run",
        resume=True,
    )

    assert history.get("listen-run").status == "completed"
