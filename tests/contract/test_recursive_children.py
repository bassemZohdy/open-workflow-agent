"""Finite native recursion retains identities and enforces root-tree quotas."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowPlan

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _services(path: Path, backend: str, *, max_depth: int, max_children: int) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "child_workflows": {"max_depth": max_depth, "max_children": max_children},
        }
    )
    return RuntimeServices(
        config, model=FakeModel({"n": 0}), database_root=None if datasource else path
    )


def _register_countdown(services: RuntimeServices) -> WorkflowPlan:
    plan = services.workflow_catalog.register(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "recursive-children",
                "name": "countdown",
                "version": "1.0.0",
            },
            "do": [
                {
                    "choose": {
                        "switch": [
                            {"positive": {"when": "${ .n > 0 }", "then": "recurse"}},
                            {"base": {"then": "finish"}},
                        ]
                    }
                },
                {
                    "recurse": {
                        "run": {
                            "workflow": {
                                "namespace": "recursive-children",
                                "name": "countdown",
                                "version": "1.0.0",
                                "input": {"n": "${ .n - 1 }"},
                            }
                        },
                        "then": "end",
                    }
                },
                {"finish": {"call": "llm:1.0.0@default", "with": {"prompt": "base case"}}},
            ],
        }
    )
    # A newer registered definition must not replace an explicit recursive pin.
    services.workflow_catalog.register(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": plan.namespace,
                "name": plan.name,
                "version": "2.0.0",
            },
            "do": [{"wrong_version": {"set": {"wrong_version": True}}}],
        }
    )
    return plan


def _tree(services: RuntimeServices, root: ExecutionHandle) -> list[ExecutionHandle]:
    rows = services.children.connection.execute(
        "SELECT child_id,depth,parent_id,parent_task_reference "
        "FROM child_workflow_bindings WHERE root_id=? ORDER BY depth",
        (root.invocation_id,),
    ).fetchall()
    saved_root = services.invocations.get(root.invocation_id)
    assert saved_root is not None
    records = [saved_root]
    for child_id, depth, parent_id, reference in rows:
        assert depth == len(records)
        assert parent_id == records[-1].invocation_id
        assert reference == "/do/1/recurse"
        child = services.invocations.get(child_id)
        assert child is not None
        assert child.parent_invocation_id == parent_id
        assert child.parent_task_reference == reference
        records.append(child)
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_finite_self_recursion_preserves_pinned_identity_and_saved_root_budget(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    services = _services(tmp_path, backend, max_depth=3, max_children=3)
    engine = engine_type()
    plan = _register_countdown(services)
    root = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        await engine.initialize(services)
        async with asyncio.timeout(30):
            result = await engine.invoke(plan, root, {"n": 3})
        assert result.status == "completed", result.error
        assert result.output == {"n": 0}
        records = _tree(services, root)
        assert len(records) == 4
        assert len({record.invocation_id for record in records}) == 4
        assert len({record.session_id for record in records}) == 4
        for index, record in enumerate(records):
            assert record.status == "completed" and record.output == {"n": 0}
            assert record.engine == engine_name
            assert record.workflow_fingerprint == plan.fingerprint
            assert record.workflow_version == "1.0.0" and record.workflow_name == plan.name
            assert record.owner_principal == "principal" and record.user_id == "correlation"
            assert services.invocations.read_input(record) == {"n": 3 - index}
            assert record.invocation_id not in engine._active
            assert record.invocation_id not in engine._ownership_tasks
        assert root.parent_invocation_id is None and root.parent_task_reference is None
        assert services.children.connection.execute(
            "SELECT max_depth,max_children,used FROM child_workflow_budgets WHERE root_id=?",
            (root.invocation_id,),
        ).fetchone() == (3, 3, 3)
        assert len(services.model.calls) == 1
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("max_depth,max_children", [(2, 10), (10, 2)])
async def test_recursive_quota_rejects_forbidden_child_before_base_model_dispatch(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    max_depth: int,
    max_children: int,
) -> None:
    services = _services(tmp_path, backend, max_depth=max_depth, max_children=max_children)
    engine = engine_type()
    plan = _register_countdown(services)
    root = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        await engine.initialize(services)
        async with asyncio.timeout(30):
            result = await engine.invoke(plan, root, {"n": 4})
        assert result.status == "faulted", result.error
        records = _tree(services, root)
        assert len(records) == 3
        assert len({record.invocation_id for record in records}) == 3
        assert len({record.session_id for record in records}) == 3
        for index, record in enumerate(records):
            assert record.status == "faulted"
            assert record.workflow_fingerprint == plan.fingerprint
            assert record.workflow_version == "1.0.0"
            assert record.owner_principal == "principal" and record.user_id == "correlation"
            assert services.invocations.read_input(record) == {"n": 4 - index}
            assert record.invocation_id not in engine._active
            assert record.invocation_id not in engine._ownership_tasks
        assert records[-1].error["message"] == "child workflow execution budget exhausted"
        assert records[-1].error["details"] == {
            "max_depth": max_depth,
            "max_children": max_children,
            "instance": "/do/1/recurse",
        }
        assert services.children.connection.execute(
            "SELECT max_depth,max_children,used FROM child_workflow_budgets WHERE root_id=?",
            (root.invocation_id,),
        ).fetchone() == (max_depth, max_children, 2)
        assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()
