"""The replay route's own checks, exercised with stub collaborators and no native engine."""

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from open_workflow_agent import replay_control_api
from open_workflow_agent.errors import InvocationNotFound, UnsupportedWorkflowFeature
from open_workflow_agent.native_extensions import LANGGRAPH_STATE_REPLAY
from open_workflow_agent.replay_control_api import mount_state_replay

REFERENCE = "a" * 64


class Controller:
    def __init__(self):
        self.verified = []
        self.admitted = []

    def verify(self, configuration):
        self.verified.append(configuration)

    async def admit(self, handle, authority, **kwargs):
        self.admitted.append((handle, authority, kwargs))
        return {
            "invocation_id": "target-1",
            "source_invocation_id": handle.invocation_id,
            "effect_policy": "reexecute",
            "state": "accepted",
        }


def build(monkeypatch, *, configured=True, capable=True, caller="alice", owner="alice"):
    controller = Controller()
    authorized = []
    verified = []
    services = SimpleNamespace(
        config=SimpleNamespace(native_state_replay="configured" if configured else None),
        native_replay_controller=controller,
        execution_authorizations=SimpleNamespace(
            verify_invocation=lambda handle: verified.append(handle.invocation_id) or "original",
            authorize_request=lambda original, action, resource: authorized.append(
                (original, action, resource)
            ),
        ),
    )
    engine = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(
            native_extensions=(LANGGRAPH_STATE_REPLAY,) if capable else ()
        )
    )
    handle = SimpleNamespace(invocation_id="source-1", owner_principal=owner)
    actions = []
    monkeypatch.setattr(
        replay_control_api,
        "current_authority",
        lambda: SimpleNamespace(principal=SimpleNamespace(identity=caller)),
    )
    app = FastAPI()
    mount_state_replay(
        app,
        services,
        engine,
        lambda action, resource: actions.append((action, resource)),
        lambda invocation_id, request: handle,
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, controller, actions, authorized, verified


def body(**extra):
    return {"reference": REFERENCE, "acknowledge_reexecution": True, **extra}


KEY = {"Idempotency-Key": "k-1"}


@pytest.mark.asyncio
async def test_admission_derives_the_request_id_and_checks_authority_before_and_after(
    monkeypatch,
):
    client, controller, actions, authorized, verified = build(monkeypatch)
    async with client:
        response = await client.post(
            "/v1/invocations/source-1/state-replays", json=body(), headers=KEY
        )
    assert response.status_code == 202, response.text
    assert response.json()["invocation_id"] == "target-1"
    ((handle, _, admitted),) = controller.admitted
    expected = hashlib.sha256(
        json.dumps([LANGGRAPH_STATE_REPLAY, "source-1", "k-1"]).encode()
    ).hexdigest()
    assert admitted == {
        "request_id": expected,
        "reference": REFERENCE,
        "acknowledge_reexecution": True,
    }
    # Authorization is evaluated before the work and again afterwards, on both authorities.
    assert actions == [("state.replay", "invocation:source-1")] * 2
    assert [item[1] for item in authorized] == ["state.replay"] * 2
    assert len(verified) == 2 and len(controller.verified) == 2


@pytest.mark.asyncio
async def test_other_principals_never_reach_the_controller(monkeypatch):
    client, controller, actions, _, _ = build(monkeypatch, caller="bob")
    async with client:
        with pytest.raises(InvocationNotFound):
            await client.post("/v1/invocations/source-1/state-replays", json=body(), headers=KEY)
    assert controller.admitted == [] and actions == []


@pytest.mark.asyncio
async def test_unavailable_profile_and_query_parameters_are_refused(monkeypatch):
    client, controller, *_ = build(monkeypatch, capable=False)
    async with client:
        with pytest.raises(UnsupportedWorkflowFeature):
            await client.post("/v1/invocations/source-1/state-replays", json=body(), headers=KEY)
    client, controller, *_ = build(monkeypatch)
    async with client:
        with pytest.raises(UnsupportedWorkflowFeature):
            await client.post(
                "/v1/invocations/source-1/state-replays?debug=1", json=body(), headers=KEY
            )
    assert controller.admitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,headers",
    [
        ({"reference": "short"}, KEY),
        (body(checkpoint_id="private"), KEY),
        (body(), {}),
        (body(), {"Idempotency-Key": "bad key!"}),
        ({"reference": REFERENCE, "acknowledge_reexecution": "yes"}, KEY),
    ],
)
async def test_malformed_requests_are_rejected_before_any_authorization(
    monkeypatch, payload, headers
):
    client, controller, actions, _, _ = build(monkeypatch)
    async with client:
        response = await client.post(
            "/v1/invocations/source-1/state-replays", json=payload, headers=headers
        )
    assert response.status_code == 422
    assert controller.admitted == [] and actions == []


@pytest.mark.asyncio
async def test_an_unconfigured_installation_exposes_no_route(monkeypatch):
    client, controller, *_ = build(monkeypatch, configured=False)
    async with client:
        response = await client.post(
            "/v1/invocations/source-1/state-replays", json=body(), headers=KEY
        )
    assert response.status_code == 404
    assert controller.admitted == []
