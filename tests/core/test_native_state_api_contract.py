"""Common state HTTP ownership, query and deadline guards use no native SDK."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from native_api_test_support import runtime
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_state import CAPABILITY, NativeStateResult
from open_workflow_agent.native_state_api import mount_native_state, native_state_capabilities


class Reader:
    def __init__(self, configuration, *, wait=False):
        self.configuration = configuration.model_dump()
        self.wait = wait
        self.queries = []
        self.verified = 0

    def verify(self, configuration):
        assert configuration.model_dump() == self.configuration
        self.verified += 1

    async def read(self, handle, plan, query):
        assert handle.workflow_fingerprint == plan.fingerprint
        self.queries.append(query)
        if self.wait:
            await asyncio.Event().wait()
        return NativeStateResult(
            json.dumps({"profile": CAPABILITY, "items": [{"data": {"ok": True}}]})
        )


def test_state_capabilities_do_not_promote_mutation_or_continuation():
    value = native_state_capabilities()
    assert value["profile"] == CAPABILITY
    assert value["action"] == "state.inspect"
    assert value["continuation"] == "not_inspected"
    assert value["updates"] is value["replay"] is value["timeTravel"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "state",
        "selected",
        "history",
        "extra",
        "duplicate",
        "anonymous",
        "other_owner",
        "missing_reader",
        "unsupported",
        "timeout",
    ],
)
async def test_state_inspection_query_owner_capability_and_deadline(tmp_path, case):
    with runtime(tmp_path) as fixture:
        services, engine, handle, actors, app, authorize, invocation, calls = fixture
        reader = Reader(services.config.native_state, wait=case == "timeout")
        services.native_state_reader = None if case == "missing_reader" else reader
        engine.extensions = () if case == "unsupported" else (CAPABILITY,)
        mount_native_state(app, services, engine, authorize, invocation)
        actor = None if case == "anonymous" else actors["bob" if case == "other_owner" else "alice"]
        suffix = "/states?limit=2&before=" + "b" * 64 if case == "history" else "/state"
        if case == "selected":
            suffix += "?reference=" + "a" * 64
        elif case == "extra":
            suffix += "?private_checkpoint_id=forbidden"
        elif case == "duplicate":
            suffix += "?reference=" + "a" * 64 + "&reference=" + "b" * 64
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with authority_scope(actor):
                response = await client.get(f"/v1/invocations/{handle.invocation_id}" + suffix)
        if case in {"state", "selected", "history"}:
            assert response.status_code == 200, response.text
            assert response.json()["items"] == [{"data": {"ok": True}}]
            assert len(reader.queries) == 1 and reader.verified == 2
            query = reader.queries[0]
            assert query.kind == ("history" if case == "history" else "state")
            assert query.reference == ("a" * 64 if case == "selected" else None)
            if case == "history":
                assert query.limit == 2 and query.before == "b" * 64
            assert len(calls) == 2
        else:
            expected = (
                404 if case in {"anonymous", "other_owner"} else 409 if case == "timeout" else 422
            )
            assert response.status_code == expected, response.text
            assert len(reader.queries) == (1 if case == "timeout" else 0)
        assert services.model.calls == []
        assert services.invocations.get(handle.invocation_id).status == "running"
