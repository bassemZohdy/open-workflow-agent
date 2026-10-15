"""OWS try/catch compensation is an explicit inverse action with its own outcome."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.workflow import compile_workflow
from test_external_operation_keys import _handle, _services, _source

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


class CompensationReceiver:
    """Record original and inverse commits separately, without deleting history."""

    def __init__(self, path: Path, outcome: str) -> None:
        self.path, self.outcome = path, outcome
        self.calls: list[tuple[str, str]] = []
        self.inverse_committed, self.release = asyncio.Event(), asyncio.Event()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE business_commits(operation_id TEXT PRIMARY KEY,action TEXT NOT NULL)"
            )
            connection.commit()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        action = json.loads(request.content)["action"]
        assert action in {"original", "inverse"}
        key = request.headers["Idempotency-Key"]
        self.calls.append((action, key))
        if action == "inverse" and self.outcome == "fault":
            return httpx.Response(503, json={"inverse_rejected": True})
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                created = connection.execute(
                    "INSERT INTO business_commits VALUES (?,?) "
                    "ON CONFLICT(operation_id) DO NOTHING",
                    (key, action),
                ).rowcount
                existing = connection.execute(
                    "SELECT action FROM business_commits WHERE operation_id=?", (key,)
                ).fetchone()[0]
                assert existing == action, "original and inverse must never share a receiver key"
        if action == "inverse":
            self.inverse_committed.set()
            if self.outcome == "uncertain":
                await self.release.wait()
            if self.outcome == "success" and created:
                # The remote inverse committed before a transient response.
                # Its explicitly declared retry must reuse that inverse's key.
                return httpx.Response(503, json={"inverse_response_lost": True})
        return httpx.Response(200, json={"action": action, "accepted": True})

    def commits(self) -> list[tuple[str, str]]:
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT action,operation_id FROM business_commits ORDER BY action"
            ).fetchall()


def _http_action(action: str) -> dict[str, Any]:
    return {
        "call": "http",
        "with": {
            "method": "POST",
            "endpoint": "https://receiver.test/" + action,
            "body": {"action": action},
        },
    }


def _plan(outcome: str) -> Any:
    inverse = _http_action("inverse")
    compensation = (
        [
            {
                "inverse_attempt": {
                    "try": [{"inverse": inverse}],
                    "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                }
            }
        ]
        if outcome == "success"
        else [{"inverse": inverse}]
    )
    if outcome == "undeclared":
        compensation = [{"report": {"set": {"compensation": "not declared"}}}]
    return compile_workflow(
        _source(
            [
                {
                    "transaction": {
                        "try": [
                            {"original": _http_action("original")},
                            {
                                "later_failure": {
                                    "raise": {
                                        "error": {
                                            "type": "urn:test:after-remote-commit",
                                            "status": 409,
                                            "title": "Controlled later failure",
                                            "detail": "Original remote action already committed",
                                        }
                                    }
                                }
                            },
                        ],
                        "catch": {"as": "original_failure", "do": compensation},
                    }
                }
            ]
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["success", "fault", "uncertain", "undeclared"])
async def test_explicit_inverse_retains_original_commit_and_its_own_durable_outcome(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, outcome: str
) -> None:
    receiver = CompensationReceiver(tmp_path / "receiver.sqlite3", outcome)
    services = _services(tmp_path, backend, receiver)
    engine = engine_type()
    plan = _plan(outcome)
    running: asyncio.Task[Any] | None = None
    try:
        await engine.initialize(services)
        handle = _handle(services, engine_name, plan)
        if outcome == "uncertain":
            running = asyncio.create_task(engine.invoke(plan, handle, {}))
            await asyncio.wait_for(receiver.inverse_committed.wait(), timeout=30)
            # Host interruption cannot establish whether the already committed
            # inverse completed. Reopen the real native/common stores instead.
            await engine.shutdown()
            await asyncio.gather(running, return_exceptions=True)
            before = services.invocations.get(handle.invocation_id)
            assert before is not None and before.status == "running"
        else:
            result = await engine.invoke(plan, handle, {})
            assert result.status == ("faulted" if outcome == "fault" else "completed"), result.error
            if outcome == "fault":
                assert result.error["details"]["status"] == 503
            elif outcome == "success":
                assert result.output == {"action": "inverse", "accepted": True}
            else:
                assert result.output == {"compensation": "not declared"}
            await engine.shutdown()
        original = [key for action, key in receiver.calls if action == "original"]
        inverse = [key for action, key in receiver.calls if action == "inverse"]
        assert len(original) == 1
        assert len(inverse) == (2 if outcome == "success" else 0 if outcome == "undeclared" else 1)
        if inverse:
            assert len(set(inverse)) == 1 and inverse[0] != original[0]
        commits = receiver.commits()
        assert ("original", original[0]) in commits
        if outcome in {"success", "uncertain"}:
            assert ("inverse", inverse[0]) in commits and len(commits) == 2
        else:
            assert commits == [("original", original[0])]
        receipt_rows = services.effect_journal.connection.execute(
            "SELECT e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity=i.identity "
            "WHERE i.invocation_id=? ORDER BY i.identity",
            (handle.invocation_id,),
        ).fetchall()
        action_receipts = [
            json.loads(row[0])
            for row in receipt_rows
            if json.loads(row[0]).get("operation_id") in {original[0], *inverse}
            and json.loads(row[0]).get("operation_id") is not None
        ]
        original_receipts = [row for row in action_receipts if row["operation_id"] == original[0]]
        inverse_receipts = [
            row for row in action_receipts if inverse and row["operation_id"] == inverse[0]
        ]
        assert [row["status"] for row in original_receipts] == ["completed"]
        assert (
            sorted(row["status"] for row in inverse_receipts)
            == {
                "success": ["completed", "faulted"],
                "fault": ["faulted"],
                "uncertain": ["pending"],
                "undeclared": [],
            }[outcome]
        )
        assert not services.model.calls
        references = [
            event.task_reference
            for event in services.events.events
            if event.event_type in {"TaskStarted", "TaskCompleted", "TaskFaulted"}
        ]
        assert references and all("visit-v1" not in reference for reference in references)
        calls_before = list(receiver.calls)
        services.close()
        services = _services(tmp_path, backend, receiver)
        engine = engine_type()
        await engine.initialize(services)
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None
        reopened = await engine.resume(saved, None, plan)
        assert reopened.status == ("faulted" if outcome in {"fault", "uncertain"} else "completed")
        if outcome == "uncertain":
            assert "unresolved execution intent" in reopened.error["message"]
        elif outcome == "fault":
            assert reopened.error == result.error
        else:
            assert reopened.output == result.output
        assert receiver.calls == calls_before and receiver.commits() == commits
        assert not services.model.calls
        recovered_rows = services.effect_journal.connection.execute(
            "SELECT e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity=i.identity "
            "WHERE i.invocation_id=? ORDER BY i.identity",
            (handle.invocation_id,),
        ).fetchall()
        assert [
            json.loads(row[0])
            for row in recovered_rows
            if json.loads(row[0]).get("operation_id") in {original[0], *inverse}
            and json.loads(row[0]).get("operation_id") is not None
        ] == action_receipts
    finally:
        receiver.release.set()
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
