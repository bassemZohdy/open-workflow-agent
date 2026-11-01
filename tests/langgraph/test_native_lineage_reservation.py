"""Replay target reservation is atomic and retries cannot change its command."""

from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError
from test_native_state_preflight import preflight as _preflight

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage
from open_workflow_agent_langgraph.native_lineage_reservation import NativeReplayReservations

preflight = _preflight
pytestmark = pytest.mark.parametrize(
    "preflight", [("sqlite", "lineage"), ("postgresql", "lineage")], indirect=True
)


@pytest.mark.asyncio
async def test_reservation_rolls_back_retries_and_detects_damage(preflight, monkeypatch):
    services, _, source, actor, reference, _, _, plan = preflight
    target_id = str(uuid4())
    prepared = await prepare_native_lineage(
        services,
        source,
        actor,
        kind="replay",
        target_invocation_id=target_id,
        request_id=uuid4().hex * 2,
        source_reference=reference,
        bounds=NativeStateConfig(timeout_seconds=30),
    )
    store = NativeReplayReservations(services)
    insert = services.invocations.create_in_transaction

    def interrupted(*args, **kwargs):
        insert(*args, **kwargs)
        raise RuntimeError("simulated interruption")

    with monkeypatch.context() as patch:
        patch.setattr(services.invocations, "create_in_transaction", interrupted)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            store.reserve(source, actor, plan, prepared)
    assert services.invocations.get(target_id) is None
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM native_replay_reservations_v1 WHERE identity=?",
            (prepared.request.identity.key,),
        ).fetchone()[0]
        == 0
    )
    target = store.reserve(source, actor, plan, prepared)
    assert store.reserve(source, actor, plan, prepared).invocation_id == target_id
    recovered, recovered_target = store.recover(source, actor, prepared.request.request_id)
    assert recovered == prepared.request and recovered_target.invocation_id == target_id
    changed = replace(prepared, request=replace(prepared.request, source_reference="a" * 64))
    with pytest.raises(OwaError):
        store.reserve(source, actor, plan, changed)
    store.connection.execute(
        "UPDATE native_replay_reservations_v1 SET digest=? WHERE identity=?",
        ("0" * 64, prepared.request.identity.key),
    )
    store.connection.commit()
    with pytest.raises(OwaError):
        store.reserve(source, actor, plan, prepared)
    with pytest.raises(OwaError):
        store.recover(source, actor, prepared.request.request_id)
    assert services.invocations.get(target.invocation_id) is not None
