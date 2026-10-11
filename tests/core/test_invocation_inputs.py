"""Original workflow input survives competing writes and reopening common storage."""

from dataclasses import replace

import pytest
from open_workflow_agent.errors import (
    ExpressionError,
    WorkflowDefinitionChanged,
    WorkflowExecutionError,
)
from open_workflow_agent.persistence import InvocationStore


def _handle(store):
    return store.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="recovery",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )


@pytest.mark.parametrize("original", [None, False, 0, "", [], {"original": [1, 2]}])
def test_input_first_claim_survives_independent_connection_and_reopening(tmp_path, original):
    path = tmp_path / "runtime.sqlite3"
    first = InvocationStore(path)
    other = InvocationStore(path)
    handle = _handle(first)
    try:
        assert first.bind_input(handle, original) == original
        assert other.bind_input(handle, {"resume": "different"}) == original
        restored = first.bind_input(handle, "different")
        if isinstance(restored, dict):
            restored["original"].append(3)
        assert other.bind_input(handle, None) == original
    finally:
        first.close()
        other.close()
    reopened = InvocationStore(path)
    try:
        assert reopened.bind_input(handle, {"resume": True}) == original
    finally:
        reopened.close()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), {1: "invalid"}, object()])
def test_input_rejects_invalid_json_without_replacing_original(invalid):
    store = InvocationStore()
    handle = _handle(store)
    try:
        with pytest.raises(ExpressionError):
            store.bind_input(handle, invalid)
        assert store.connection.execute("SELECT COUNT(*) FROM invocation_inputs").fetchone()[0] == 0
        assert store.bind_input(handle, {"original": True}) == {"original": True}
        with pytest.raises(ExpressionError):
            store.bind_input(handle, invalid)
        assert store.bind_input(handle, None) == {"original": True}
    finally:
        store.close()


def test_input_definition_identity_cannot_be_rebound():
    store = InvocationStore()
    handle = _handle(store)
    try:
        store.bind_input(handle, None)
        with pytest.raises(WorkflowDefinitionChanged):
            store.bind_input(replace(handle, workflow_fingerprint="changed"), {})
        assert store.bind_input(handle, {}) is None
    finally:
        store.close()


@pytest.mark.parametrize("payload", ['{"secret":"must-not-leak"', "NaN"])
def test_corrupt_record_fails_closed_without_raw_data(payload):
    store = InvocationStore()
    handle = _handle(store)
    try:
        store.bind_input(handle, None)
        store.connection.execute(
            "UPDATE invocation_inputs SET payload = ? WHERE invocation_id = ?",
            (payload, handle.invocation_id),
        )
        store.connection.commit()
        with pytest.raises(WorkflowExecutionError, match="invocation input could not be recovered"):
            store.bind_input(handle, {"replacement": True})
        saved = store.connection.execute(
            "SELECT payload FROM invocation_inputs WHERE invocation_id = ?", (handle.invocation_id,)
        ).fetchone()[0]
        assert saved == payload
    finally:
        store.close()


def test_unavailable_storage_has_common_recovery_error():
    store = InvocationStore()
    handle = _handle(store)
    store.close()
    with pytest.raises(WorkflowExecutionError, match="invocation input could not be recovered"):
        store.bind_input(handle, {})


def test_failed_terminal_commit_preserves_running_handle_and_persisted_result(
    tmp_path, monkeypatch
):
    store = InvocationStore(tmp_path / "runtime.sqlite3")
    handle = _handle(store)

    def fail_commit(_connection):
        raise RuntimeError("database-password-must-not-leak")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(type(store.connection), "commit", fail_commit)
            with pytest.raises(WorkflowExecutionError) as failure:
                store.update(handle, status="completed", output={"result": True})
            assert failure.value.message == "invocation state could not be stored"
        assert handle.status == "running" and handle.output is None
        persisted = store.get(handle.invocation_id)
        assert persisted is not None and persisted.status == "running" and persisted.output is None
        store.update(handle, status="completed", output={"result": True})
        persisted = store.get(handle.invocation_id)
        assert persisted is not None and persisted.output == {"result": True}
    finally:
        store.close()
