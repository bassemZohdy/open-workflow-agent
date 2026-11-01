"""Declared re-execution requires an explicit acknowledgement before native reads."""

from uuid import uuid4

import pytest
from open_workflow_agent.errors import OwaError
from test_native_state_preflight import counts
from test_native_state_preflight import preflight as _preflight

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import native_lineage_preflight as module

preflight = _preflight


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "preflight",
    [("sqlite", "lineage", "reexecute"), ("postgresql", "lineage", "reexecute")],
    indirect=True,
)
async def test_reexecution_acknowledgement_is_pinned_without_dispatching_work(
    preflight, monkeypatch
):
    services, _engine, handle, actor, reference, *_ = preflight
    options = dict(
        kind="replay",
        target_invocation_id=uuid4().hex,
        request_id=uuid4().hex * 2,
        source_reference=reference,
    )
    before = counts(preflight)
    attempts = []

    def forbidden(*args):
        attempts.append(True)
        raise AssertionError("native reader reached before acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(module, "readonly_saver", forbidden)
        with pytest.raises(OwaError):
            await module.prepare_native_lineage(services, handle, actor, **options)
    assert attempts == []
    proposal = await module.prepare_native_lineage(
        services, handle, actor, acknowledge_reexecution=True, **options
    )
    assert proposal.request.effect_policy == "reexecute"
    assert proposal.request.acknowledge_reexecution is True
    assert counts(preflight) == before and services.model.calls == []
