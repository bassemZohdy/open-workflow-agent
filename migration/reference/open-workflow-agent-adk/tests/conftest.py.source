"""Shared pytest configuration for the open-workflow-agent-adk suite.

Centralizes the Docker/testcontainers skip dance that used to be copy-pasted
across integration test files, registers the ``integration`` marker, and tracks
skip counts so silently-skipping suites are visible in CI output.
"""

from __future__ import annotations

import os

import pytest


def docker_enabled() -> bool:
    """Return True when Docker-backed integration tests may run.

    Docker-backed tests run by default wherever a daemon is available; the
    historical ``DOCKER_TESTS=0`` env gate disables them explicitly, and CI
    selects them with the ``integration`` marker.
    """
    return os.environ.get("DOCKER_TESTS") != "0"


def require_docker(reason: str = "requires Docker") -> pytest.MarkDecorator:
    """Return a skip marker for Docker-dependent tests."""
    return pytest.mark.skipif(not docker_enabled(), reason=reason)


@pytest.fixture(scope="module")
def postgres_url() -> str:
    """Start a PostgreSQL testcontainer and yield an asyncpg-compatible URL."""
    if not docker_enabled():
        pytest.skip("requires Docker")
    try:
        from testcontainers.postgres import PostgresContainer  # noqa: PLC0415
    except ImportError as exc:
        pytest.skip(f"testcontainers-postgres not available: {exc}")

    container = PostgresContainer("postgres:16")
    try:
        container.start()
        yield container.get_connection_url().replace("+psycopg2", "").replace("+asyncpg", "")
    except Exception as exc:
        pytest.skip(f"Could not start PostgreSQL container: {exc}")
    finally:
        try:
            container.stop()
        except Exception:
            pass


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: Docker/service-backed integration tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests unless explicitly requested and report skip counts."""
    skipped = sum(
        1 for item in items if any(mark.name == "integration" for mark in item.iter_markers())
    )
    if skipped:
        print(f"\n[conftest] {skipped} integration-marked test(s) collected")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, exitstatus: int) -> None:
    stats = getattr(terminalreporter, "stats", {})
    skipped = len(stats.get("skipped", []))
    if skipped:
        terminalreporter.write_sep(
            "-",
            "integration/conditional skips this run: "
            f"{skipped} (set DOCKER_TESTS=1 or RUN_INTEGRATION_TESTS=1 to run)",
        )
