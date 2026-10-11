import inspect

from google.adk.sessions import DatabaseSessionService


def test_database_session_backend_is_available_for_durable_runs() -> None:
    assert "db_url" in inspect.signature(DatabaseSessionService).parameters
