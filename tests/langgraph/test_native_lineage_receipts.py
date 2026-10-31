"""Only receipts present at the selected historical checkpoint are inherited."""

from uuid import uuid4

import pytest
from open_workflow_agent.config import NativeStateConfig
from test_native_state_preflight import preflight as _preflight

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_lineage_inspection import selected_business_receipts
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage
from open_workflow_agent_langgraph.native_state import state_reference

preflight = _preflight
pytestmark = pytest.mark.parametrize(
    "preflight", [("sqlite", "lineage-effects"), ("postgresql", "lineage-effects")], indirect=True
)


@pytest.mark.asyncio
async def test_earlier_root_excludes_later_real_model_receipt(preflight):
    import json

    services, _engine, source, actor, current, _item, saver_type, plan = preflight
    assert len(services.model.calls) == 1
    root = LangGraphScopeExecutor(plan, None, source.invocation_id).checkpoint_config("/do", 1)
    async with saver_type.from_conn_string(
        services.engine_database_path("langgraph", create_namespace=False)
    ) as saver:
        items = [item async for item in saver.alist(root)]
        early = next(
            item
            for item in items
            if "snapshot" in item.checkpoint["channel_values"]
            and json.loads(item.checkpoint["channel_values"]["snapshot"])["task_visits"]
            == {"/do/0/first": 1}
        )
    early_reference = state_reference(
        source.invocation_id, plan.fingerprint, early.checkpoint["id"]
    )

    async def prepare(reference):
        return await prepare_native_lineage(
            services,
            source,
            actor,
            kind="replay",
            target_invocation_id=uuid4().hex,
            request_id=uuid4().hex * 2,
            source_reference=reference,
            bounds=NativeStateConfig(timeout_seconds=30),
        )

    before = await prepare(early_reference)
    after = await prepare(current)
    assert selected_business_receipts(before, services.effect_journal) == ()
    receipts = selected_business_receipts(after, services.effect_journal)
    assert len(receipts) == 1
    assert receipts[0].identity.operation == "call:agent:1.0.0@default"
    assert receipts[0].identity.invocation_id == source.invocation_id
    assert len(services.model.calls) == 1
