from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the shared tests/engine_cases.py helper importable from every test
# directory (tests/contract, tests/ctk, ...) without putting "tests" on the
# pytest pythonpath, where its engine-named subdirectories would shadow the
# real installed native packages. Appending keeps site-packages ahead of it.
sys.path.append(str(Path(__file__).resolve().parent))

from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


@pytest.fixture
def services(tmp_path):
    value = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    yield value
    value.close()
