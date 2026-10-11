"""Fixtures shared by resource tests that use mocked remote endpoints."""

import pytest


@pytest.fixture(autouse=True)
def allow_mocked_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt resource mocks into unresolved test hostnames without weakening SSRF tests."""
    monkeypatch.setenv("WORKFLOW_EGRESS_SKIP_DNS", "1")
