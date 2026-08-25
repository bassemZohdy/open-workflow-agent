from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


@pytest.fixture
def services(tmp_path):
    value = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    yield value
    value.close()
