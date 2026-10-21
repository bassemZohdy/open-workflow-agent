"""Only the qualified embedded bus grants the generic event durability claim."""

from __future__ import annotations

from pathlib import Path

import pytest
from engine_cases import engine_cases
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import ApprovalEventBus
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import WorkflowEngine
from open_workflow_agent.events import EventBus, InMemoryEventBus
from open_workflow_agent.services import RuntimeServices


class _UnqualifiedClaimedDurableBus(InMemoryEventBus):
    """A custom boolean claim supplies no embedded acceptance evidence."""

    durable = True


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("approvals_enabled", [False, True])
@pytest.mark.parametrize(
    "backend,expected_durable",
    [("embedded", True), ("memory", False), ("custom-memory", False), ("custom-claimed", False)],
)
def test_api_qualifies_only_embedded_event_delegate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: type[WorkflowEngine],
    approvals_enabled: bool,
    backend: str,
    expected_durable: bool,
) -> None:
    monkeypatch.setenv("OWA_CAPABILITY_OPERATOR_TOKEN", "capability-test-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "events": {"backend": "memory" if backend == "memory" else "embedded"},
            "security": {
                "profiles": {
                    "operator": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_CAPABILITY_OPERATOR_TOKEN"},
                    }
                }
            },
            "approvals": {
                "enabled": approvals_enabled,
                "operator_security_profile": "operator",
            },
        }
    )
    custom: EventBus | None = None
    if backend == "custom-memory":
        custom = InMemoryEventBus()
    elif backend == "custom-claimed":
        custom = _UnqualifiedClaimedDurableBus()
    services = RuntimeServices(config, database_root=tmp_path, model=FakeModel(), event_bus=custom)
    engine = engine_type()
    with TestClient(create_app(config=config, services=services, engine=engine)) as client:
        response = client.get("/v1/capabilities")
        assert response.status_code == 200
        capabilities = response.json()
        assert capabilities["engine"] == engine_name
        assert capabilities["features"]["events"] == {
            "emit": True,
            "listen": True,
            "durable": expected_durable,
        }
        assert isinstance(services.event_bus, ApprovalEventBus) is approvals_enabled
        assert capabilities["features"]["approvals"] == {
            "approval": approvals_enabled,
            "durable": approvals_enabled,
            "replay": approvals_enabled,
            "operatorAuthorization": "bearer" if approvals_enabled else None,
        }
        assert capabilities["features"]["cloudEvents"] == {
            "lifecycle": True,
            "specversion": "1.0",
            "delivery": "bounded_snapshot",
            "durable": False,
        }
        native = engine.capabilities().as_dict()["features"]
        assert native["events"] == {"emit": True, "listen": True, "durable": False}
        for feature in ("resume", "streaming", "cancellation", "waiting", "nativeTaskExecution"):
            assert capabilities["features"][feature] == native[feature]
