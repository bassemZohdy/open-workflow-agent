"""Reserved lineage targets participate in the caller's transaction."""

import sqlite3
from uuid import uuid4

import pytest
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.persistence import InvocationStore


def _create(store, identity):
    return store.create_in_transaction(
        store.connection,
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name="replay",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
        input_data={"original": True},
        reserved_invocation_id=identity,
    )


def test_reserved_identity_rolls_back_with_input_and_cannot_collide(tmp_path):
    store = InvocationStore(tmp_path / "reserved.sqlite")
    identity = str(uuid4())
    try:
        handle = _create(store, identity)
        assert handle.invocation_id == identity
        store.connection.raw.rollback()
        assert store.get(identity) is None
        assert store.connection.execute("SELECT COUNT(*) FROM invocation_inputs").fetchone()[0] == 0
        handle = _create(store, identity)
        store.connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _create(store, identity)
        store.connection.raw.rollback()
        assert store.get(identity) is not None
        assert store.read_input(handle) == {"original": True}
    finally:
        store.close()


@pytest.mark.parametrize("identity", ["", "not-a-uuid", uuid4().hex, str(uuid4()).upper(), 1])
def test_invalid_reserved_identity_is_rejected_before_insertion(identity):
    store = InvocationStore()
    try:
        with pytest.raises(InvocationStateError):
            _create(store, identity)
        assert store.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0] == 0
    finally:
        store.close()
