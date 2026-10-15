"""Private ADK child requests bind original input and common admission receipts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk.native import ADK_AVAILABLE, AdkRecoveryContext, NativeAdkRunner

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not ADK_AVAILABLE, reason="native ADK dependency is absent"),
]


@pytest.mark.parametrize(
    "corruption",
    [None, "missing-admission", "changed-child", "changed-deadline", "unrecorded-wake"],
)
async def test_native_child_request_replays_only_with_verified_admission_and_wake(
    tmp_path: Path, corruption: str | None
) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    journal = services.effect_journal
    native = NativeAdkRunner()
    parent_plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "parent",
                "version": "1.0.0",
            },
            "do": [
                {
                    "child": {
                        "run": {
                            "workflow": {"namespace": "native", "name": "child", "version": "1.0.0"}
                        }
                    }
                }
            ],
        }
    )
    child_plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "child",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    services.invocations.bind_input(parent, {"original": True})
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    identity = EffectIdentity(
        parent.invocation_id,
        parent_plan.fingerprint,
        "/do/0/child",
        (("/do/0/child", 1),),
        "run:workflow",
    )
    child = services.children.reserve(identity, parent, child_plan, {})
    registration = services.child_waits.arm(
        identity,
        parent,
        child,
        workflow_source=parent_plan.source,
        deadline_at=1000,
    )
    suspension = registration.suspension
    policy_identity = replace(identity, operation="policy:child-await:v1")
    receipt = registration.receipt
    metadata: dict[str, Any] = {}
    context = AdkRecoveryContext(
        identity.invocation_id,
        identity.workflow_fingerprint,
        frozenset({identity.task_reference}),
        journal,
        metadata,
        child_waits=services.child_waits,
    )
    common: dict[str, Any] = {
        "session_id": "parent-session",
        "user_id": None,
        "invocation_id": "native-parent-execution",
        "database_path": str(tmp_path / "adk.sqlite3"),
        "_recovery_context": context,
    }
    calls: list[str] = []

    async def runner(value: Any) -> Any:
        assert value == {"original": True}
        calls.append("run")
        if identity.key in metadata.get("_child_wakes", {}):
            return {"child_output": True}
        await metadata["_effect_receipt_observer"](policy_identity, receipt)
        await metadata["_waiting_checkpoint_observer"]()
        raise suspension

    try:
        with pytest.raises(ChildSuspension):
            await native.invoke(runner, {"original": True}, **common)
        assert calls == ["run"]
        await native.admit_resume({"original": True}, **common)
        # No wake cannot re-enter the portable callback or allocate another child.
        with pytest.raises(ChildSuspension):
            await native.resume(runner, {"original": True}, **common)
        assert calls == ["run"]
        services.invocations.update(child, status="completed", output={"child_output": True})
        wake = services.child_waits.wake(registration, parent, now=900)
        assert wake is not None and wake["reason"] == "child_terminal"
        metadata["_child_wakes"] = {identity.key: dict(wake)}
        if corruption == "missing-admission":
            journal.connection.execute(
                "DELETE FROM child_workflow_waits WHERE run_key=?",
                (identity.key,),
            )
            journal.connection.commit()
        elif corruption == "changed-child":
            metadata["_child_wakes"][identity.key]["child_id"] = str(uuid4())
        elif corruption == "changed-deadline":
            metadata["_child_wakes"][identity.key]["deadline_at"] = 1001.0
        elif corruption == "unrecorded-wake":
            journal.connection.execute(
                "DELETE FROM workflow_effects WHERE identity=?",
                (replace(identity, operation="policy:child-wake:v1").key,),
            )
            journal.connection.execute(
                "UPDATE child_workflow_waits SET state='pending' WHERE run_key=?",
                (identity.key,),
            )
            journal.connection.commit()
        if corruption is not None:
            with pytest.raises(InvocationStateError):
                await native.resume(runner, {"original": True}, **common)
            assert calls == ["run"]
        else:
            assert await native.resume(runner, {"original": True}, **common) == {
                "child_output": True
            }
            assert calls == ["run", "run"]
            await native.admit_resume({"original": True}, **common)
            with pytest.raises(InvocationStateError):
                await native.admit_resume({"original": False}, **common)
    finally:
        services.execution_ownership.release(parent._execution_lease)
        services.close()


@pytest.mark.parametrize(
    "other_proof",
    [
        "valid",
        "nested-valid",
        "other-fork",
        "nested-other-fork",
        "other-occurrence",
        "nested-other-occurrence",
        "missing-native-admission",
        "consumed-old-wake",
    ],
)
async def test_native_fork_can_rescan_recorded_sibling_without_waking_interrupted_child(
    tmp_path: Path, other_proof: str
) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    native = NativeAdkRunner()
    parent_plan = compile_workflow(
        {
            "document": {"dsl": "1.0.3", "namespace": "native", "name": "fork", "version": "1.0.0"},
            "do": [
                {
                    "race": {
                        "fork": {
                            "compete": True,
                            "branches": [
                                {
                                    "start": {
                                        "run": {
                                            "workflow": {
                                                "namespace": "native",
                                                "name": "child",
                                                "version": "1.0.0",
                                            }
                                        }
                                    }
                                },
                                {
                                    "start": {
                                        "run": {
                                            "workflow": {
                                                "namespace": "native",
                                                "name": "child",
                                                "version": "1.0.0",
                                            }
                                        }
                                    }
                                },
                            ],
                        }
                    }
                }
            ],
        }
    )
    if other_proof.startswith("nested-"):
        parent_source = parent_plan.source
        branches = parent_source["do"][0]["race"]["fork"]["branches"]
        branches[0] = {"inner": {"fork": {"branches": [branches[0]]}}}
        parent_plan = compile_workflow(parent_source)
    child_plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "child",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    services.invocations.bind_input(parent, {})
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)

    def identity(branch: int) -> EffectIdentity:
        fork = (
            "/do/0/other"
            if branch == 1 and other_proof in {"other-fork", "nested-other-fork"}
            else "/do/0/race"
        )
        visit = (
            2
            if branch == 1
            and other_proof
            in {
                "other-occurrence",
                "nested-other-occurrence",
            }
            else 1
        )
        path: tuple[tuple[str, int], ...] = ((fork, visit), (fork + "/@attempt", 1))
        if branch == 0 and other_proof.startswith("nested-"):
            fork += "/fork/branches/0/inner"
            path += ((fork, 1), (fork + "/@attempt", 1))
        reference = fork + f"/fork/branches/{branch}/start"
        return EffectIdentity(
            parent.invocation_id,
            parent_plan.fingerprint,
            reference,
            path + ((reference, 1), (reference + "/@attempt", 1)),
            "run:workflow",
        )

    registrations = []
    for branch in range(2):
        run_identity = identity(branch)
        child = services.children.reserve(run_identity, parent, child_plan, {})
        registrations.append(
            services.child_waits.arm(
                run_identity,
                parent,
                child,
                workflow_source=parent_plan.source,
                deadline_at=None,
            )
        )
    first, other = registrations
    metadata: dict[str, Any] = {}
    context = AdkRecoveryContext(
        parent.invocation_id,
        parent_plan.fingerprint,
        frozenset(value.identity.task_reference for value in registrations),
        services.effect_journal,
        metadata,
        child_waits=services.child_waits,
    )
    common: dict[str, Any] = {
        "session_id": parent.session_id,
        "user_id": None,
        "invocation_id": "native-parent-fork",
        "database_path": str(tmp_path / "adk.sqlite3"),
        "_recovery_context": context,
    }
    calls = []

    async def runner(value: Any) -> Any:
        assert value == {}
        calls.append("run")
        if other.identity.key in metadata.get("_child_wakes", {}):
            return {"winner": "other-child"}
        for registration in registrations:
            if registration is other and other_proof == "missing-native-admission":
                continue
            admission = replace(registration.identity, operation="policy:child-await:v1")
            await metadata["_effect_receipt_observer"](admission, registration.receipt)
        await metadata["_waiting_checkpoint_observer"]()
        raise first.suspension

    try:
        with pytest.raises(ChildSuspension):
            await native.invoke(runner, {}, **common)
        with pytest.raises(ChildSuspension):
            await native.resume(runner, {}, **common)
        assert calls == ["run"]
        other_child = services.invocations.get(other.suspension.child_id)
        assert other_child is not None
        services.invocations.update(
            other_child, status="completed", output={"winner": "other-child"}
        )
        wake = services.child_waits.wake(other, parent, now=900)
        assert wake is not None
        if other_proof == "consumed-old-wake":
            services.child_waits.consume(other, parent)
        metadata["_child_wakes"] = {other.identity.key: wake}
        if other_proof == "missing-native-admission":
            with pytest.raises(InvocationStateError):
                await native.resume(runner, {}, **common)
            assert calls == ["run"]
        elif other_proof not in {"valid", "nested-valid"}:
            with pytest.raises(ChildSuspension):
                await native.resume(runner, {}, **common)
            assert calls == ["run"]
        else:
            assert await native.resume(runner, {}, **common) == {"winner": "other-child"}
            assert calls == ["run", "run"]
            await native.admit_resume({}, **common)
        # The private acknowledgement has created no common wake for first.
        known = services.child_waits.known_wakes(parent.invocation_id, parent_plan.fingerprint)
        assert set(known) == {other.identity.key}
        first_child = services.invocations.get(first.suspension.child_id)
        assert first_child is not None and first_child.status == "running"
    finally:
        services.execution_ownership.release(parent._execution_lease)
        services.close()
