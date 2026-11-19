"""Original-owner recovery controls exercise real common records and leases."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from native_api_test_support import runtime
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_recovery_api import mount_memory_recovery
from test_memory_writes import NativeEvidence


async def seed(fixture, *, committed=False):
    services, engine, handle, actors, *_ = fixture
    binding = MemoryReadBinding("notes", "1.0.0", "sqlite")
    evidence = NativeEvidence(binding)
    evidence.fail = "after" if committed else "before"
    services.memory_writes.register(evidence.backend())
    services.native_memory_call_guard = lambda reference: None
    parent = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/0/noop",
        (("/do/0/noop", 1),),
        "call:memory.put:1.0.0@notes",
    )
    mutation = MemoryMutation.put(
        parent,
        binding,
        "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest(),
        "notes",
        "record",
        {"private": "retained value"},
    )
    async with engine._own_execution(handle):
        services.execution_authorizations.authorize(
            handle,
            parent,
            "catalog.call",
            "memory.put:1.0.0@notes",
            effect_request_digest("permission", {}),
            execution_lease=handle._execution_lease,
        )
        services.effect_journal.begin(
            parent,
            effect_request_digest(
                "memory.put:1.0.0@notes", {"key": "record", "value": {"private": "retained value"}}
            ),
            "first",
            execution_lease=handle._execution_lease,
        )
        with pytest.raises(InvocationStateError):
            await services.memory_writes.mutate(handle, mutation)
    return mutation, evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_inspect_resolve_repeat_and_terminal_acknowledgement_never_resend(
    tmp_path, committed
):
    with runtime(tmp_path) as fixture:
        services, engine, handle, actors, app, authorize, invocation, _ = fixture
        mutation, evidence = await seed(fixture, committed=committed)
        mount_memory_recovery(app, services, engine, authorize, invocation)
        path = f"/v1/invocations/{handle.invocation_id}/memory-writes"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with authority_scope(actors["alice"]):
                metadata = await client.get(path)
                assert metadata.status_code == 200, metadata.text
                writes = metadata.json()["writes"]
                assert len(writes) == 1 and writes[0]["state"] == "dispatched"
                assert writes[0]["operation_id"] == mutation.identity.key
                assert "private" not in metadata.text and "retained value" not in metadata.text
                target = path + "/" + mutation.identity.key + "/resolve"
                payload = {
                    "request_digest": mutation.request_digest,
                    "decision": "complete" if committed else "abandon_unknown",
                    "acknowledge_unknown": not committed,
                }
                response = await client.post(
                    target, json=payload, headers={"Idempotency-Key": "choice-1"}
                )
                assert response.status_code == 200, response.text
                repeated = await client.post(
                    target, json=payload, headers={"Idempotency-Key": "choice-1"}
                )
                assert repeated.json() == response.json()
                inspected = await client.get(path)
                assert inspected.json()["writes"][0]["state"] == (
                    "completed" if committed else "abandoned"
                )
                async with engine._own_execution(handle):
                    services.invocations.update(handle, status="completed", output={"done": True})
                terminal = await client.post(
                    target, json=payload, headers={"Idempotency-Key": "choice-1"}
                )
                assert terminal.status_code == 200 and terminal.json() == response.json(), (
                    terminal.text
                )
                changed = await client.post(
                    target, json=payload, headers={"Idempotency-Key": "choice-2"}
                )
                assert changed.status_code == 409, changed.text
        assert evidence.calls == [mutation.identity.key]
        assert services.invocations.get(handle.invocation_id).status == "completed"
        assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "anonymous",
        "other_owner",
        "guard",
        "missing",
        "digest",
        "parent",
        "acknowledgement",
        "terminal",
    ],
)
async def test_invalid_resolution_cannot_change_or_redispatch_unknown_write(tmp_path, damage):
    with runtime(tmp_path) as fixture:
        services, engine, handle, actors, app, authorize, invocation, calls = fixture
        mutation, evidence = await seed(fixture)
        mount_memory_recovery(app, services, engine, authorize, invocation)
        actor = (
            None if damage == "anonymous" else actors["bob" if damage == "other_owner" else "alice"]
        )
        operation_id = "0" * 64 if damage == "missing" else mutation.identity.key
        payload = {
            "request_digest": "0" * 64 if damage == "digest" else mutation.request_digest,
            "decision": "abandon_unknown",
            "acknowledge_unknown": damage != "acknowledgement",
        }
        if damage == "guard":
            services.native_memory_call_guard = None
        elif damage == "parent":
            services.effect_journal.connection.execute(
                "DELETE FROM workflow_effects WHERE identity=?", (mutation.parent.key,)
            )
            services.effect_journal.connection.commit()
        elif damage == "terminal":
            async with engine._own_execution(handle):
                services.invocations.update(handle, status="completed", output=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with authority_scope(actor):
                response = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/memory-writes/{operation_id}/resolve",
                    json=payload,
                    headers={"Idempotency-Key": "choice-1"},
                )
        expected = (
            404
            if damage in {"anonymous", "other_owner", "missing"}
            else 422
            if damage == "guard"
            else 409
        )
        assert response.status_code == expected, response.text
        assert "retained value" not in response.text
        assert evidence.calls == [mutation.identity.key]
        if damage in {"anonymous", "other_owner"}:
            assert calls == []
