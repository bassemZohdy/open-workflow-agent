"""Deterministic protected approval fixtures using actual common/backend services."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT, APPROVAL_REQUEST_EVENT
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices

ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
ACTORS = {"reviewer-a": "actor-a", "reviewer-b": "actor-b", "lead": "actor-lead"}
TOKENS = {actor: "fixture-token-" + actor for actor in ACTORS}


def configuration(backend):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL datasource is required")
    profiles = {}
    for actor, name in ACTORS.items():
        env = "OWA_PROTECTED_APPROVAL_" + name.upper().replace("-", "_")
        os.environ[env] = TOKENS[actor]
        profiles[name] = {"type": "bearer", "principal": actor, "token": {"from_env": env}}
    return RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "security": {"profiles": profiles},
            "approvals": {
                "enabled": True,
                "protected": True,
                "actor_security_profiles": list(ACTORS.values()),
                "poll_interval_seconds": 0.05,
                "scan_limit": 8,
            },
        }
    )


def runtime(path, backend, *, config=None, business=None):
    services = RuntimeServices(
        config or configuration(backend),
        model=FakeModel(),
        database_root=None if backend == "postgresql" else path,
        recovery_profile_provider=lambda engine: {
            "id": "protected-approval-fixture",
            "version": "1",
        },
    )
    assert (
        services.invocations.connection.kind
        == services.effect_journal.connection.kind
        == services.event_store.connection.kind
        == backend
    )
    if backend == "postgresql":
        assert services.database_root is None
        for engine in ("adk", "langgraph"):
            assert services.engine_database_path(engine, create_namespace=False).startswith(
                "postgres"
            )
    if business is not None:

        async def dispatch(request):
            assert request.method == "POST" and str(request.url) == "https://business.test/action"
            body = json.loads(request.content)
            business.append(body)
            return httpx.Response(
                200,
                json={"decision": body["approval"]["decision"], "value": body["approval"]["value"]},
            )

        services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(dispatch)))
        services.tools.protocols = services.protocols
    return services


def actor(services, identity):
    principal = services.approvals.authorize_actor("Bearer " + TOKENS[identity])
    assert principal.identity == identity
    return principal


def expiry(seconds=120):
    return datetime.fromtimestamp(time.time() + seconds, UTC).isoformat().replace("+00:00", "Z")


def definition(subject, expires_at, *, schema=False):
    policy = {
        "profile": "owa/protected-approval/v1",
        "assignee": "reviewer-a",
        "reassignmentTargets": ["reviewer-b"],
        "escalationTargets": ["lead"],
    }
    if schema:
        policy["responseSchema"] = {
            "format": "json",
            "document": {
                "type": "object",
                "properties": {"ticket": {"type": "string"}},
                "required": ["ticket"],
                "additionalProperties": False,
            },
        }
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "protected-approvals",
            "name": "original",
            "version": "1.0.0",
        },
        "timeout": {"after": {"seconds": 45}},
        "do": [
            {
                "request": {
                    "emit": {
                        "event": {
                            "with": {
                                "id": subject,
                                "source": "urn:fixture:protected-approvals",
                                "type": APPROVAL_REQUEST_EVENT,
                                "subject": subject,
                                "approvalexpiresat": expires_at,
                                "data": {"approvalPolicy": policy, "question": "continue?"},
                            },
                        }
                    }
                }
            },
            {
                "decision": {
                    "listen": {
                        "to": {
                            "one": {"with": {"type": APPROVAL_DECISION_EVENT, "subject": subject}}
                        },
                        "read": "data",
                    }
                }
            },
            {
                "route": {
                    "switch": [
                        {
                            "approved": {
                                "when": '${ .[0].decision == "approved" }',
                                "then": "business",
                            }
                        },
                        {"blocked": {"then": "end"}},
                    ]
                }
            },
            {
                "business": {
                    "call": "http",
                    "with": {
                        "method": "post",
                        "endpoint": "https://business.test/action",
                        "body": {"request": "${ $workflow.input }", "approval": "${ .[0] }"},
                    },
                }
            },
        ],
    }


def handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id="original-session",
        user_id="original-user",
        owner_principal="requester",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def actual_native(engine, engine_name):
    if not (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    ):
        pytest.skip("actual independent native SDK environment required")


async def waiting(services, invocation, task):
    async with asyncio.timeout(30):
        while services.invocations.get(invocation.invocation_id).status != "waiting":
            if task.done():
                raise AssertionError(await task)
            await asyncio.sleep(0.01)


def identities(services, invocation_id):
    return [
        EffectIdentity.from_value(json.loads(row[0]))
        for row in services.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effect_invocations "
            "WHERE invocation_id=? ORDER BY identity",
            (invocation_id,),
        ).fetchall()
    ]


def approval_snapshot(services, subject, invocation_id, *, include_owner=True):
    store = services.approvals.protected_store
    return tuple(
        services.effect_journal.connection.execute(sql, args).fetchall()
        for sql, args in (
            ("SELECT * FROM " + store.requests + " WHERE approval_id=?", (subject,)),
            ("SELECT * FROM " + store.audit + " WHERE approval_id=? ORDER BY revision", (subject,)),
            (
                "SELECT * FROM event_inbox WHERE event_id=? OR envelope LIKE ? ORDER BY sequence",
                (subject, "%" + subject + "%"),
            ),
            ("SELECT * FROM invocation_ownership WHERE invocation_id=?", (invocation_id,)),
            (
                "SELECT * FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity",
                (invocation_id,),
            ),
            (
                "SELECT * FROM workflow_effects WHERE identity IN "
                "(SELECT identity FROM workflow_effect_invocations WHERE invocation_id=?) "
                "ORDER BY identity",
                (invocation_id,),
            ),
        )
        if include_owner or "invocation_ownership" not in sql
    )
