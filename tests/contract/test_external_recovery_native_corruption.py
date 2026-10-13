"""Actual SDK waiting checkpoints retain external admission and outcome trust."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _external_receiver import PRINCIPAL, RECEIVER_ID, TOKEN, TOKEN_ENV, receiver
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.external_recovery import (
    ADMISSION_OPERATION,
    RECOVERY_OPERATION,
    HttpExternalActionRecovery,
    TrustedHttpReceiver,
)
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from test_native_checkpoint_fencing import _snapshot as _native_snapshot

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _services(path: Path, datasource: str | None, remote: Any) -> RuntimeServices:
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else path,
    )
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "receiver": {
                    "type": "bearer",
                    "principal": PRINCIPAL,
                    "token": {"from_env": TOKEN_ENV},
                }
            }
        }
    )
    http = HttpClient(
        timeout=5,
        max_response_bytes=65536,
        ca_bundle=str(remote.certificate),
        allowed_hosts={"127.0.0.1"},
        authentication=ProfileAuthentication(security, "receiver"),
    )
    services.protocols = ProtocolServices(http)
    services.tools.protocols = services.protocols
    services.external_action_recovery = HttpExternalActionRecovery(
        services.effect_journal,
        services.invocations,
        http,
        (
            TrustedHttpReceiver(
                RECEIVER_ID, remote.business_endpoint, remote.lookup_endpoint, "receiver", PRINCIPAL
            ),
        ),
    )
    return services


def _snapshot(services: RuntimeServices, invocation_id: str) -> tuple[Any, ...]:
    connection = services.effect_journal.connection
    return (
        services.invocations.get(invocation_id)._persisted_payload,
        connection.execute(
            "SELECT identity,payload,state,due_at,wake_at FROM workflow_timers "
            "WHERE invocation_id=? ORDER BY identity",
            (invocation_id,),
        ).fetchall(),
        connection.execute(
            "SELECT e.identity,e.owner,e.status,e.payload FROM workflow_effects e "
            "JOIN workflow_effect_invocations i ON i.identity=e.identity "
            "WHERE i.invocation_id=? ORDER BY e.identity",
            (invocation_id,),
        ).fetchall(),
    )


_CASES = [
    (phase, damage)
    for phase in ("delivered", "reconciled")
    for damage in ("missing-admission", "changed-admission", "admission-index", "action-index")
] + [("reconciled", damage) for damage in ("missing-proof", "changed-proof", "recovery-index")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("phase,damage", _CASES)
async def test_actual_native_checkpoint_rejects_missing_or_changed_external_trust_before_wake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    phase: str,
    damage: str,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent actual native environment is required")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with receiver(tmp_path, hold_response=phase == "reconciled") as remote:
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "external-negative",
                    "name": "waiting-after-action",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "action": {
                            "call": "http",
                            "with": {
                                "method": "POST",
                                "endpoint": remote.business_endpoint,
                                "body": "${ $workflow.input }",
                            },
                        }
                    },
                    {"pause": {"wait": {"seconds": 60}}},
                    {"after": {"call": "llm:1.0.0@default"}},
                ],
            }
        )
        services = _services(tmp_path / "runtime", datasource, remote)
        engine = engine_type()
        running = None
        original = {"original": True, "case": phase + ":" + damage}
        try:
            await engine.initialize(services)
            parent = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id="correlation",
                owner_principal="caller",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            if phase == "reconciled":
                running = asyncio.create_task(engine.invoke(plan, parent, original))
                async with asyncio.timeout(20):
                    while not remote.committed.is_set():
                        assert not running.done()
                        await asyncio.sleep(0.01)
                assert parent.status == "running" and remote.commits() == 1
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
                await engine.shutdown()
                services.close()
                remote.release.set()
                services = _services(tmp_path / "runtime", datasource, remote)
                engine = engine_type()
                await engine.initialize(services)
                parent = services.invocations.get(parent.invocation_id)
                result = await asyncio.wait_for(engine.resume(parent, {"not": "input"}, plan), 30)
            else:
                result = await asyncio.wait_for(engine.invoke(plan, parent, original), 30)
            assert result.status == "waiting", result.error
            assert remote.commits() == 1 and services.model.calls == []
            rows = services.effect_journal.connection.execute(
                "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
                (parent.invocation_id,),
            ).fetchall()
            identity = next(
                EffectIdentity.from_value(json.loads(row[0]))
                for row in rows
                if json.loads(row[0])["operation"] == "call:http"
            )
            receipt = services.effect_journal.get(identity)
            assert receipt is not None and receipt.status == "completed"
            assert (receipt.external_outcome is not None) == (phase == "reconciled")
            await engine.shutdown()
            services.close()
            services = _services(tmp_path / "runtime", datasource, remote)
            engine = engine_type()
            connection = services.effect_journal.connection
            if damage == "missing-admission":
                connection.execute(
                    "DELETE FROM external_http_admissions WHERE identity=?", (identity.key,)
                )
            elif damage == "changed-admission":
                connection.execute(
                    "UPDATE external_http_admissions SET digest='changed' WHERE identity=?",
                    (identity.key,),
                )
            elif damage.endswith("index"):
                operation = {
                    "admission-index": ADMISSION_OPERATION,
                    "action-index": "call:http",
                    "recovery-index": RECOVERY_OPERATION,
                }[damage]
                connection.execute(
                    "DELETE FROM workflow_effect_invocations WHERE identity=?",
                    (replace(identity, operation=operation).key,),
                )
            elif damage == "missing-proof":
                connection.execute(
                    "DELETE FROM external_http_outcomes WHERE identity=?", (identity.key,)
                )
            else:
                connection.execute(
                    "UPDATE external_http_outcomes SET digest='changed' WHERE identity=?",
                    (identity.key,),
                )
            connection.commit()
            await engine.initialize(services)
            parent = services.invocations.get(parent.invocation_id)
            before, requests = _snapshot(services, parent.invocation_id), list(remote.requests)
            native_before = _native_snapshot(services, parent, backend)
            with pytest.raises(
                InvocationStateError,
                match="native execution state|workflow action recovery evidence",
            ):
                await asyncio.wait_for(engine.resume(parent, {"must": "not-wake"}, plan), 30)
            assert _snapshot(services, parent.invocation_id) == before
            assert _native_snapshot(services, parent, backend) == native_before
            assert remote.requests == requests and remote.commits() == 1
            assert services.invocations.read_input(parent) == original
            assert services.model.calls == []
        finally:
            remote.release.set()
            if running is not None:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            await engine.shutdown()
            services.close()
