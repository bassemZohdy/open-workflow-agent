"""Independent deterministic role providers and actual native pattern services."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml
from _version_recovery_worker import native_snapshot
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timing import root_clock_identity
from open_workflow_agent.workflow import compile_workflow
from test_execution_authority_isolation import configuration as authority_configuration

ROOT = Path(__file__).resolve().parents[2]
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
PATTERNS = (
    "assistant",
    "pipeline",
    "multi_perspective",
    "refine_until_good",
    "expert_dispatch",
    "team_coordinator",
    "plan_and_execute",
    "approval_gate",
)
FILES = {pattern: pattern.replace("_", "-") for pattern in PATTERNS}
FILES.update(assistant="assistant-roles", pipeline="pipeline-roles")


def source(pattern):
    return yaml.safe_load(
        (ROOT / "resources/patterns" / (FILES.get(pattern, pattern) + ".yaml")).read_text(
            encoding="utf-8"
        )
    )


def role_configuration(name, *, tools=(), **changes):
    return {
        "id": name,
        "version": "1.0.0",
        "model": {"provider": "fake", "name": "fake/" + name},
        "tools": list(tools),
        "instruction": "Deployment-owned " + name + " role.",
        "max_rounds": 4,
        "max_model_calls": 4,
        "max_tool_calls": 4,
        "max_child_invocations": 0,
        "max_output_bytes": 4096,
        **changes,
    }


def configuration(backend, roles, *, approvals=False, **changes):
    original = authority_configuration(backend).model_dump(mode="json", exclude_unset=True)
    original["agent"] = {"roles": copy.deepcopy(roles)}
    original.update(changes)
    if approvals:
        profiles = original["security"]["profiles"]
        for actor in ("reviewer", "alternate", "lead"):
            variable = "OWA_PATTERN_ACTOR_" + actor.upper()
            os.environ[variable] = "fixture-actor-" + actor
            profiles[actor] = {
                "type": "bearer",
                "principal": actor,
                "token": {"from_env": variable},
            }
        original["approvals"] = {
            "enabled": True,
            "protected": True,
            "actor_security_profiles": ["reviewer", "alternate", "lead"],
            "poll_interval_seconds": 0.05,
            "scan_limit": 8,
        }
    return RuntimeConfig.model_validate(original)


def runtime(path, backend, roles, models, *, config=None, dispatch=None):
    services = RuntimeServices(
        config or configuration(backend, roles),
        model=FakeModel(),
        role_models=models,
        database_root=None if backend == "postgresql" else path,
        recovery_profile_provider=lambda engine: {"id": "agent-pattern-fixture", "version": "1"},
    )
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    if backend == "postgresql":
        assert services.database_root is None
        for engine in ("adk", "langgraph"):
            assert services.engine_database_path(engine, create_namespace=False).startswith(
                "postgres"
            )
    if dispatch is not None:
        services.protocols = ProtocolServices(
            HttpClient(transport=httpx.MockTransport(dispatch)),
            security=services.config.security,
            authorize_destination=services.authorize_protocol_destination,
            authorize_credential=services.authorize_protocol_credential,
        )
        services.tools.protocols = services.protocols
    return services


def original_authority(services):
    return ExecutionAuthority.capture(
        static_principal(services.config.security.profile("actor-alice")),
        security_profile="actor-alice",
    )


def create(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=uuid4().hex,
        user_id="pattern-correlation",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def actual_native(engine, engine_name):
    if not (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    ):
        pytest.skip("actual independently installed native SDK required")


async def invoke(engine, services, plan, handle, original):
    with authority_scope(original_authority(services)):
        return await engine.invoke(plan, handle, copy.deepcopy(original))


def receipts(services, handle):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity",
        (handle.invocation_id,),
    ).fetchall()
    return [
        (identity, services.effect_journal.get(identity))
        for row in rows
        if (identity := EffectIdentity.from_value(json.loads(row[0])))
    ]


def retained(services, handle, backend):
    selected = receipts(services, handle)
    clock = services.effect_journal.get(
        root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
    )
    return {
        "input": services.invocations.read_input(handle),
        "owner": handle.owner_principal,
        "fingerprint": handle.workflow_fingerprint,
        "authority": services.execution_authorizations.verify_invocation(handle).as_value(),
        "clock": clock.output if clock is not None else None,
        "receipts": {
            identity.key: ReceiptDependency.capture(identity, receipt).as_value()
            for identity, receipt in selected
            if receipt.status != "pending"
        },
        "native": native_snapshot(services, handle, backend),
    }


def compiled(services, pattern):
    return compile_workflow(source(pattern), enabled_function_calls=services.role_function_calls)


def register_workers(services):
    for name in ("researcher-worker", "finance-worker"):
        services.workflow_catalog.register(compiled(services, name))


def scripts(pattern):
    return copy.deepcopy(
        {
            "assistant": {"assistant": [{"answer": "assistant"}]},
            "pipeline": {"drafter": [{"text": "draft"}], "reviewer": [{"text": "refined"}]},
            "multi_perspective": {
                "researcher": [{"answer": "research"}],
                "risk": [{"answer": "risk"}],
                "synthesizer": [{"answer": "combined"}],
            },
            "refine_until_good": {
                "drafter": [{"text": "first"}, {"text": "second"}],
                "critic": [
                    {"quality": False, "feedback": "improve"},
                    {"quality": True, "feedback": "accepted"},
                ],
            },
            "expert_dispatch": {
                "router": [{"route": "research"}],
                "researcher": [{"answer": "research"}],
                "finance": [{"answer": "finance"}],
            },
            "team_coordinator": {
                "researcher": [{"answer": "research"}],
                "finance": [{"answer": "finance"}],
                "synthesizer": [{"answer": "team"}],
            },
            "plan_and_execute": {
                "planner": [
                    {
                        "actions": [
                            {"action": "research", "input": "research input"},
                            {"action": "finance", "input": "finance input"},
                        ]
                    }
                ],
                "researcher": [{"answer": "research"}],
                "finance": [{"answer": "finance"}],
            },
            "approval_gate": {"approver-action": [{"answer": "approved action"}]},
        }[pattern]
    )


def providers(pattern, *, script=None, responses=None):
    selected = scripts(pattern) if script is None else copy.deepcopy(script)
    models = {
        name: FakeModel((responses or {}).get(name, values)) for name, values in selected.items()
    }
    roles = {name: role_configuration(name) for name in models}
    return roles, models


def expected(pattern):
    return copy.deepcopy(
        {
            "assistant": {"answer": "assistant"},
            "pipeline": {"text": "refined"},
            "multi_perspective": {"answer": "combined"},
            "refine_until_good": {"text": "second", "quality": True},
            "expert_dispatch": {"answer": "research"},
            "team_coordinator": {"answer": "team"},
            "plan_and_execute": {
                "results": [
                    {"action": "research", "output": {"answer": "research"}},
                    {"action": "finance", "output": {"answer": "finance"}},
                ]
            },
            "approval_gate": {"answer": "approved action"},
        }[pattern]
    )


def expected_inputs(pattern, question="hello"):
    return copy.deepcopy(
        {
            "assistant": {"assistant": [question]},
            "pipeline": {"drafter": [question], "reviewer": [{"text": "draft"}]},
            "multi_perspective": {
                "researcher": [question],
                "risk": [question],
                "synthesizer": [
                    {
                        "question": question,
                        "perspectives": [{"answer": "research"}, {"answer": "risk"}],
                    }
                ],
            },
            "refine_until_good": {
                "drafter": [
                    {"question": question, "previous": None, "feedback": None},
                    {"question": question, "previous": "first", "feedback": "improve"},
                ],
                "critic": [
                    {"question": question, "text": "first"},
                    {"question": question, "text": "second"},
                ],
            },
            "expert_dispatch": {"router": [question], "researcher": [question], "finance": []},
            "team_coordinator": {
                "researcher": [question],
                "finance": [question],
                "synthesizer": [
                    {
                        "question": question,
                        "workers": [{"answer": "research"}, {"answer": "finance"}],
                    }
                ],
            },
            "plan_and_execute": {
                "planner": [question],
                "researcher": ["research input"],
                "finance": ["finance input"],
            },
        }[pattern]
    )


def assert_roles(models, pattern, *, question="hello"):
    for role, values in expected_inputs(pattern, question).items():
        assert [prompt["input"] for prompt in models[role].calls] == values
        assert all(prompt["tools"] == [] for prompt in models[role].calls)
