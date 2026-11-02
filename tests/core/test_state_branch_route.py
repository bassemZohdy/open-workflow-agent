"""Engine-neutral original-owner checks for branch admission."""

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from open_workflow_agent import branch_control_api
from open_workflow_agent.branch_control_api import mount_state_branch
from open_workflow_agent.errors import InvocationNotFound, UnsupportedWorkflowFeature
from open_workflow_agent.native_extensions import LANGGRAPH_STATE_BRANCH


class Controller:
    def __init__(self):
        self.verified = []
        self.admitted = []

    def verify(self, config):
        self.verified.append(config)

    async def admit(self, handle, authority, **kwargs):
        self.admitted.append((handle, authority, kwargs))
        return {
            "invocation_id": "target",
            "source_invocation_id": handle.invocation_id,
            "effect_policy": "reexecute",
            "state": "accepted",
        }


def build(monkeypatch, *, configured=True, capable=True, caller="alice"):
    controller = Controller()
    actions, original_actions = [], []
    services = SimpleNamespace(
        config=SimpleNamespace(native_state_branch="configured" if configured else None),
        native_branch_controller=controller,
        execution_authorizations=SimpleNamespace(
            verify_invocation=lambda _handle: "original",
            authorize_request=lambda original, action, resource: original_actions.append(
                (original, action, resource)
            ),
        ),
    )
    engine = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(
            native_extensions=(LANGGRAPH_STATE_BRANCH,) if capable else ()
        )
    )
    handle = SimpleNamespace(invocation_id="source", owner_principal="alice")
    monkeypatch.setattr(
        branch_control_api,
        "current_authority",
        lambda: SimpleNamespace(principal=SimpleNamespace(identity=caller)),
    )
    app = FastAPI()
    mount_state_branch(
        app,
        services,
        engine,
        lambda action, resource: actions.append((action, resource)),
        lambda _id, _request: handle,
    )
    return (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"),
        controller,
        actions,
        original_actions,
    )


def body():
    return {
        "reference": "a" * 64,
        "task_reference": "/do/1/second",
        "values": {"value": 7},
        "acknowledge_reexecution": True,
    }


@pytest.mark.asyncio
async def test_original_and_current_authority_are_checked_before_and_after(monkeypatch):
    client, controller, actions, original = build(monkeypatch)
    async with client:
        response = await client.post(
            "/v1/invocations/source/state-branches", json=body(), headers={"Idempotency-Key": "key"}
        )
    assert response.status_code == 202
    admitted = controller.admitted[0][2]
    assert admitted == {
        "request_id": hashlib.sha256(
            json.dumps([LANGGRAPH_STATE_BRANCH, "source", "key"]).encode()
        ).hexdigest(),
        **body(),
    }
    assert actions == [("state.branch", "invocation:source")] * 2
    assert original == [("original", "state.branch", "invocation:source")] * 2
    assert len(controller.verified) == 2


@pytest.mark.asyncio
async def test_unconfigured_foreign_and_unavailable_profiles_cannot_admit(monkeypatch):
    path = "/v1/invocations/source/state-branches"
    key = {"Idempotency-Key": "key"}
    client, controller, *_ = build(monkeypatch, configured=False)
    async with client:
        assert (await client.post(path, json=body(), headers=key)).status_code == 404
    assert controller.admitted == []
    client, controller, *_ = build(monkeypatch, caller="bob")
    async with client:
        with pytest.raises(InvocationNotFound):
            await client.post(path, json=body(), headers=key)
    assert controller.admitted == []
    client, controller, *_ = build(monkeypatch, capable=False)
    async with client:
        with pytest.raises(UnsupportedWorkflowFeature):
            await client.post(path, json=body(), headers=key)
    assert controller.admitted == []


@pytest.mark.asyncio
async def test_duplicate_keys_and_query_parameters_cannot_reach_controller(monkeypatch):
    path = "/v1/invocations/source/state-branches"
    client, controller, *_ = build(monkeypatch)
    async with client:
        with pytest.raises(UnsupportedWorkflowFeature):
            await client.post(
                path, json=body(), headers=[("Idempotency-Key", "a"), ("Idempotency-Key", "b")]
            )
        with pytest.raises(UnsupportedWorkflowFeature):
            await client.post(path + "?debug=1", json=body(), headers={"Idempotency-Key": "key"})
    assert controller.admitted == []
