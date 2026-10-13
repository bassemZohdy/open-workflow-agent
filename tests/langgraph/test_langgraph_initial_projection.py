"""SDK initializer write corruption cannot become an initial delivery."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("langgraph.graph")

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.pregel._algo import _xxhash_str
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import InvocationStateError, WorkflowExecutionError
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.execution_authorization import authority_identity
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

pytestmark = pytest.mark.asyncio


def snapshot(services: RuntimeServices, native_path: str) -> tuple[Any, Any]:
    common = tuple(
        (table, services.effect_journal.connection.execute(f"SELECT * FROM {table}").fetchall())
        for table in (
            "invocations",
            "invocation_inputs",
            "invocation_ownership",
            "workflow_effects",
            "workflow_effect_invocations",
            "recovery_definitions",
            "recovery_invocation_definitions",
            "initial_invocation_deliveries",
            "execution_authorities",
        )
    )
    with closing(
        sqlite3.connect(Path(native_path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        native = tuple(
            (table, connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall())
            for table in ("checkpoints", "writes")
        )
    return common, native


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "write-value",
        "write-task-id",
        "write-route",
        "missing-write",
        "extra-write",
        "advanced-input",
        "checkpoint-format",
        "common-pending",
        "small-channel-fraction",
        "zero-channel-fraction",
        "channel-format",
        "channel-advanced",
        "original-authority",
        "authority-row",
        "authority-receipt",
        "authority-index",
        "authority-missing-native",
        "authority-foreign-scope-policy",
    ],
)
async def test_initializer_pending_writes_require_original_scope_and_sdk_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str | None
) -> None:
    authenticated = corruption == "original-authority" or str(corruption).startswith("authority-")
    config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "initial": {
                        "type": "bearer",
                        "principal": "initial-owner",
                        "roles": ["worker"],
                        "scopes": ["execute"],
                        "audience": ["runtime"],
                        "token": {"from_env": "OWA_INITIALIZER_UNUSED_TOKEN"},
                    }
                }
            }
        }
        if authenticated
        else {}
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    business_calls: list[Any] = []

    async def forbidden(request: Any) -> Any:
        business_calls.append(request)
        raise AssertionError("initializer inspection must not dispatch business work")

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(forbidden)))
    await engine.initialize(services)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "initial",
                "version": "1.0.0",
            },
            "timeout": {"after": {"seconds": 60}},
            "do": [
                {
                    "action": {
                        "call": "http",
                        "with": {
                            "method": "POST",
                            "endpoint": "https://initial.test/action",
                            "body": "${ $workflow.input }",
                        },
                    }
                }
            ],
        }
    )
    handle = services.invocations.create(
        engine="langgraph",
        session_id="initial-session",
        user_id="initial-user",
        owner_principal="initial-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    original_input = {"original": True}
    try:
        with authority_scope(
            ExecutionAuthority.capture(
                static_principal(config.security.profile("initial")), security_profile="initial"
            )
            if authenticated
            else None
        ):
            await engine.prepare_initial_delivery(plan, handle, original_input, skill_id="initial")
        if authenticated:
            assert services.execution_authorizations.authority_receipt(handle) is not None

        async def before_any_task(*args: Any, **kwargs: Any) -> None:
            raise WorkflowExecutionError("capture the actual SDK initializer")

        with monkeypatch.context() as capture:
            capture.setattr(engine.executor, "_run_task_item", before_any_task)
            with pytest.raises(WorkflowExecutionError):
                await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    original_input,
                    metadata={
                        "invocation_id": handle.invocation_id,
                        **({"_invocation_handle": handle} if authenticated else {}),
                    },
                    invocation_id=handle.invocation_id,
                )

        async def publish_initial(saver: Any) -> None:
            driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
            config = driver.checkpoint_config("/do", 1)
            initial = await anext(
                item async for item in saver.alist(config) if item.metadata["step"] == -1
            )
            checkpoint = copy.deepcopy(initial.checkpoint)
            checkpoint["id"] = empty_checkpoint()["id"]
            metadata = copy.deepcopy(initial.metadata)
            state = checkpoint["channel_values"]["__start__"]
            writes = [
                (channel, copy.deepcopy(value)) for _, channel, value in initial.pending_writes
            ]
            assert len(writes) == 5 and set(channel for channel, _ in writes) == {
                *state,
                "branch:to:"
                + "task_"
                + hashlib.sha256(plan.tasks[0].reference.encode()).hexdigest(),
            }
            starter = _xxhash_str(
                bytes.fromhex(checkpoint["id"].replace("-", "")),
                "__start__",
                "0",
                "__start__",
                "__pregel_pull",
                "__start__",
            )
            if corruption == "write-value":
                writes = [(name, "{}" if name == "snapshot" else value) for name, value in writes]
            elif corruption == "write-task-id":
                starter = "foreign-starter"
            elif corruption == "write-route":
                writes = [
                    ("branch:to:foreign" if name.startswith("branch:") else name, value)
                    for name, value in writes
                ]
            elif corruption == "missing-write":
                writes = [(name, value) for name, value in writes if name != "receipt_bindings"]
            elif corruption == "extra-write":
                writes.append(("__resume__", []))
            elif corruption == "advanced-input":
                value = json.loads(state["snapshot"])
                value["task_visits"] = {plan.tasks[0].reference: 1}
                value["outputs"] = {"action": "advanced"}
                state["snapshot"] = json.dumps(value)
                bindings = json.loads(state["receipt_bindings"])
                bindings["state_digest"] = driver._state_digest(state)
                state["receipt_bindings"] = json.dumps(bindings)
                writes = [(name, state[name] if name in state else value) for name, value in writes]
            elif corruption == "checkpoint-format":
                checkpoint["v"] = 5
            elif corruption in {"small-channel-fraction", "zero-channel-fraction"}:
                fraction = 0.000001 if corruption == "small-channel-fraction" else 0.0
                checkpoint["channel_versions"]["__start__"] = f"{1:032}.{fraction:016}"
            elif corruption == "channel-format":
                checkpoint["channel_versions"]["__start__"] = f"{1:032}.0.10"
            elif corruption == "channel-advanced":
                checkpoint["channel_versions"]["__start__"] = f"{2:032}.{0.5:016}"
            elif corruption == "authority-missing-native":
                bindings = json.loads(state["receipt_bindings"])
                bindings["receipts"] = [
                    value
                    for value in bindings["receipts"]
                    if value["identity"]["operation"] != authority_identity(handle).operation
                ]
                state["receipt_bindings"] = json.dumps(bindings)
                bindings["state_digest"] = driver._state_digest(state)
                state["receipt_bindings"] = json.dumps(bindings)
                writes = [(name, state[name] if name in state else value) for name, value in writes]
            stored = await saver.aput(config, checkpoint, metadata, checkpoint["channel_versions"])
            await saver.aput_writes(stored, writes, starter)

        # This setup publishes an actual SDK initializer through its serializer,
        # then applies a deliberate saved-evidence mutation. The independent
        # process-stop acceptance fixture remains the positive crash witness.
        await engine.native._with_saver(publish_initial)
        if corruption in {"authority-row", "authority-receipt", "authority-index"}:
            table, column, value = {
                "authority-row": ("execution_authorities", "invocation_id", handle.invocation_id),
                "authority-receipt": (
                    "workflow_effects",
                    "identity",
                    authority_identity(handle).key,
                ),
                "authority-index": (
                    "workflow_effect_invocations",
                    "identity",
                    authority_identity(handle).key,
                ),
            }[corruption]
            services.effect_journal.connection.execute(
                f"DELETE FROM {table} WHERE {column}=?", (value,)
            )
            services.effect_journal.connection.commit()
        elif corruption == "authority-foreign-scope-policy":
            lease = services.execution_ownership.acquire(handle, seconds=60)
            try:
                identity = EffectIdentity(
                    handle.invocation_id,
                    plan.fingerprint,
                    "/do",
                    (("/do", 1),),
                    "policy:foreign-initial-admission:v1",
                )
                digest = effect_request_digest("foreign", {})
                services.effect_journal.begin(
                    identity, digest, "foreign-policy", execution_lease=lease
                )
                services.effect_journal.finish(identity, "foreign-policy", output={})
            finally:
                services.execution_ownership.release(lease)
        if corruption == "common-pending":
            lease = services.execution_ownership.acquire(handle, seconds=60)
            try:
                identity = EffectIdentity(
                    handle.invocation_id,
                    plan.fingerprint,
                    plan.tasks[0].reference,
                    ((plan.tasks[0].reference, 1),),
                    "call:http",
                )
                services.effect_journal.begin(
                    identity,
                    effect_request_digest("http", {}),
                    "previous-business-owner",
                    execution_lease=lease,
                )
            finally:
                services.execution_ownership.release(lease)
        native_path = engine.native.database_path
        assert isinstance(native_path, str)
        before = snapshot(services, native_path)
        if corruption in {
            None,
            "small-channel-fraction",
            "zero-channel-fraction",
            "original-authority",
        }:
            assert not await engine._scheduled_native_state_exists(handle, plan)
        else:
            with pytest.raises(InvocationStateError):
                await engine.dispatch_initial_delivery(handle, lambda target, original: None)
        assert snapshot(services, native_path) == before
        assert business_calls == [] and services.model.calls == []
        assert (
            not engine._active and not engine._execution_leases and handle._execution_lease is None
        )
    finally:
        await engine.shutdown()
        services.close()
