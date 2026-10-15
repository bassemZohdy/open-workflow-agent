"""Native terminal receipt links retain immutable reservation and policy proof."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import (
    ChildInvocationDependency,
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
    _receipt_payload,
    effect_request_digest,
)
from open_workflow_agent.errors import InvocationStateConflict, WorkflowExecutionError
from open_workflow_agent.persistence import ExecutionHandle, InvocationStore
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.workflow import WorkflowPlan, compile_workflow

TerminalFixture = tuple[
    RuntimeServices, ExecutionHandle, ExecutionHandle, EffectIdentity, EffectReceipt
]


def _model_calls(services: RuntimeServices) -> list[Any]:
    assert isinstance(services.model, FakeModel)
    return services.model.calls


def _saved_payload(services: RuntimeServices, invocation_id: str) -> str:
    saved = services.invocations.get(invocation_id)
    assert saved is not None and isinstance(saved._persisted_payload, str)
    return saved._persisted_payload


def _plan(name: str) -> WorkflowPlan:
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "terminal-proof",
                "name": name,
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def terminal(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Iterator[
    tuple[RuntimeServices, ExecutionHandle, ExecutionHandle, EffectIdentity, EffectReceipt]
]:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {
                "persistence": {"datasource": datasource},
                "child_workflows": {"max_depth": 2, "max_children": 3},
            }
        ),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    parent_plan, child_plan = _plan("parent"), _plan("child")
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    services.invocations.bind_input(parent, {"original": True})
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    identity = EffectIdentity(
        parent.invocation_id,
        parent_plan.fingerprint,
        "/do/0/start",
        (("/do/0/start", 1),),
        "run:workflow",
    )
    child = services.children.reserve(identity, parent, child_plan, {"original": True})
    services.invocations.update(child, status="completed", output={"terminal": True})
    digest = effect_request_digest(
        "run:workflow",
        {
            "workflow_fingerprint": child_plan.fingerprint,
            "input": {"original": True},
        },
    )
    services.effect_journal.begin(
        identity, digest, "parent-action", execution_lease=parent._execution_lease
    )
    dependency = ChildInvocationDependency.capture(services.invocations, child.invocation_id)
    receipt = services.effect_journal.finish(
        identity,
        "parent-action",
        output=child.output,
        child_invocation=dependency,
    )
    try:
        yield services, parent, child, identity, receipt
    finally:
        services.execution_ownership.release(parent._execution_lease)
        services.close()


def test_new_terminal_receipt_roundtrip_retains_private_binding_and_policy_markers(
    terminal: TerminalFixture,
) -> None:
    services, _, child, identity, receipt = terminal
    dependency = receipt.child_invocation
    assert dependency is not None
    assert dependency.binding_digest is not None and dependency.budget_digest is not None
    assert ChildInvocationDependency.from_value(asdict(dependency)) == dependency
    native = ReceiptDependency.capture(identity, receipt)
    native.verify(services.effect_journal)
    assert services.effect_journal.get(identity) == receipt
    current = services.invocations.get(child.invocation_id)
    assert current is not None and current.output == {"terminal": True}
    assert _model_calls(services) == []


@pytest.mark.parametrize(
    "damage",
    [
        "missing-binding",
        "missing-budget",
        "changed-policy",
        "rewritten-policy",
        "rewritten-binding",
        "changed-count",
    ],
)
def test_native_terminal_dependency_rejects_deleted_or_changed_provenance_without_writes(
    terminal: TerminalFixture,
    damage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, parent, child, identity, receipt = terminal
    connection = services.effect_journal.connection
    native = ReceiptDependency.capture(identity, receipt)
    if damage == "missing-binding":
        connection.execute(
            "DELETE FROM child_workflow_bindings WHERE binding_key=?", (identity.key,)
        )
    elif damage == "missing-budget":
        connection.execute(
            "DELETE FROM child_workflow_budgets WHERE root_id=?", (parent.invocation_id,)
        )
    elif damage in {"changed-policy", "rewritten-policy"}:
        connection.execute(
            "UPDATE child_workflow_budgets SET max_depth=64 WHERE root_id=?",
            (parent.invocation_id,),
        )
        if damage == "rewritten-policy":
            policy = connection.execute(
                "SELECT fingerprint,max_depth,max_children FROM child_workflow_budgets "
                "WHERE root_id=?",
                (parent.invocation_id,),
            ).fetchone()
            connection.execute(
                "UPDATE child_workflow_budgets SET policy_digest=? WHERE root_id=?",
                (services.children._digest((parent.invocation_id, *policy)), parent.invocation_id),
            )
    elif damage == "rewritten-binding":
        row = list(
            connection.execute(
                "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,source_json,"
                "digest,parent_task_reference "
                "FROM child_workflow_bindings WHERE binding_key=?",
                (identity.key,),
            ).fetchone()
        )
        source = json.loads(row[6])
        source["do"][0]["finish"]["set"]["changed"] = True
        row[6] = json.dumps(source)
        connection.execute(
            "UPDATE child_workflow_bindings SET source_json=?,digest=? WHERE binding_key=?",
            (row[6], services.children._digest((*row[:7], row[8])), identity.key),
        )
    else:
        connection.execute(
            "UPDATE child_workflow_budgets SET used=0 WHERE root_id=?", (parent.invocation_id,)
        )
    connection.commit()
    before = connection.execute(
        "SELECT payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()[0]
    parent_before = _saved_payload(services, parent.invocation_id)
    child_before = _saved_payload(services, child.invocation_id)
    queries: list[str] = []
    execute = StorageConnection.execute

    def read_only(store: StorageConnection, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        queries.append(query)
        assert query.lstrip().upper().startswith("SELECT"), (
            "terminal admission must not write or prepare storage"
        )
        return execute(store, query, parameters)

    monkeypatch.setattr(StorageConnection, "execute", read_only)
    with pytest.raises(WorkflowExecutionError):
        native.verify(services.effect_journal)
    assert queries
    assert (
        connection.execute(
            "SELECT payload FROM workflow_effects WHERE identity=?", (identity.key,)
        ).fetchone()[0]
        == before
    )
    assert _saved_payload(services, parent.invocation_id) == parent_before
    assert _saved_payload(services, child.invocation_id) == child_before
    assert _model_calls(services) == []
    # Fixture teardown releases its lease; keep the read-only assertion scoped
    # precisely to admission rather than forbidding that necessary control write.
    monkeypatch.undo()


def test_later_sibling_reservation_does_not_invalidate_original_policy_marker(
    terminal: TerminalFixture,
) -> None:
    services, parent, _, identity, receipt = terminal
    another = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/1/other",
        (("/do/1/other", 1),),
        "run:workflow",
    )
    services.children.reserve(another, parent, _plan("other"), {"next": True})
    ReceiptDependency.capture(identity, receipt).verify(services.effect_journal)
    assert services.effect_journal.get(identity) == receipt


def test_actual_parent_lease_takeover_fences_old_child_reconciliation_and_preserves_first_outcome(
    terminal: TerminalFixture,
    tmp_path: Path,
) -> None:
    services, parent, _, _, _ = terminal
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/1/recovery",
        (("/do/1/recovery", 1),),
        "run:workflow",
    )
    child_plan = _plan("recovery-child")
    original = {"recovery": True}
    child = services.children.reserve(identity, parent, child_plan, original)
    services.invocations.update(child, status="completed", output={"first": "terminal"})
    digest = effect_request_digest(
        "run:workflow",
        {
            "workflow_fingerprint": child_plan.fingerprint,
            "input": original,
        },
    )
    pending = services.effect_journal.begin(
        identity, digest, "original-intent-owner", execution_lease=parent._execution_lease
    )
    other = RuntimeServices(
        services.config,
        model=FakeModel(),
        database_root=None if services.config.persistence.datasource else tmp_path,
    )
    current_lease = None
    try:
        second_handle = other.invocations.get(parent.invocation_id)
        assert second_handle is not None
        assert parent._execution_lease is not None
        old_lease = services.execution_ownership.renew(parent._execution_lease, seconds=0.05)
        parent._execution_lease = old_lease
        deadline = time.monotonic() + 5
        # The common database clock expires a real issued lease. No generation,
        # owner or fabricated expired timestamp is injected by the fixture.
        while current_lease is None:
            try:
                current_lease = other.execution_ownership.acquire(second_handle, seconds=300)
            except InvocationStateConflict:
                assert time.monotonic() < deadline, "the real short lease did not expire"
                time.sleep(0.01)
        assert current_lease.generation == old_lease.generation + 1
        assert current_lease.owner != old_lease.owner
        with pytest.raises(InvocationStateConflict):
            services.effect_journal.finish_child(
                identity,
                digest,
                child.invocation_id,
                execution_lease=old_lease,
                output={"wrong": "old-worker"},
            )
        assert other.effect_journal.get(identity) == pending
        current_child = other.invocations.get(child.invocation_id)
        assert current_child is not None and current_child.output == {"first": "terminal"}
        completed = other.effect_journal.finish_child(
            identity,
            digest,
            child.invocation_id,
            execution_lease=current_lease,
            output=child.output,
        )
        assert completed.owner == "original-intent-owner"
        assert completed.output == {"first": "terminal"}
        assert (
            completed.child_invocation is not None
            and completed.child_invocation.binding_digest is not None
        )
        duplicate = other.effect_journal.finish_child(
            identity,
            digest,
            child.invocation_id,
            execution_lease=current_lease,
            output={"wrong": "duplicate"},
        )
        assert duplicate == completed
        with pytest.raises(InvocationStateConflict):
            services.effect_journal.finish_child(
                identity,
                digest,
                child.invocation_id,
                execution_lease=old_lease,
                output={"wrong": "late-old-worker"},
            )
        assert other.effect_journal.get(identity) == completed
        assert _model_calls(services) == _model_calls(other) == []
    finally:
        if current_lease is not None:
            # The existing fixture releases this same real successor at teardown.
            parent._execution_lease = current_lease
        other.close()


def test_legacy_child_payload_and_native_hash_remain_exact_without_creating_binding_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    store = InvocationStore(path)
    journal = EffectJournal(path, invocations=store)
    parent = store.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name="parent",
        workflow_version="1.0.0",
        workflow_fingerprint="parent-fingerprint",
    )
    child = store.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name="child",
        workflow_version="1.0.0",
        workflow_fingerprint="child-fingerprint",
        parent_invocation_id=parent.invocation_id,
        parent_task_reference="/do/0/start",
    )
    store.bind_input(child, {"legacy": True})
    store.update(child, status="completed", output={"legacy": "output"})
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/0/start",
        (("/do/0/start", 1),),
        "run:workflow",
    )
    digest = effect_request_digest(
        "run:workflow",
        {"workflow_fingerprint": child.workflow_fingerprint, "input": {"legacy": True}},
    )
    try:
        dependency = ChildInvocationDependency.capture(store, child.invocation_id)
        assert dependency.binding_digest is dependency.budget_digest is None
        journal.begin(identity, digest, "legacy-owner")
        receipt = journal.finish(
            identity, "legacy-owner", output=child.output, child_invocation=dependency
        )
        legacy_value = {
            "invocation_id": child.invocation_id,
            "record_digest": dependency.record_digest,
        }
        assert _receipt_payload(receipt)["child_invocation"] == legacy_value
        # The legacy ABI is fixed independently of future optional dataclass
        # fields. It never included an absent external-outcome extension.
        expected = {
            "request_digest": digest,
            "owner": "legacy-owner",
            "status": "completed",
            "output": {"legacy": "output"},
            "error": None,
            "error_status_code": None,
            "operation_id": None,
            "child_invocation": legacy_value,
        }
        encoded = json.dumps(
            expected, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        assert (
            ReceiptDependency.capture(identity, receipt).receipt_digest
            == hashlib.sha256(encoded).hexdigest()
        )
        assert journal.get(identity) == receipt
        assert ChildInvocationDependency.from_value(legacy_value) == dependency
        assert (
            store.connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'child_workflow_%'"
            ).fetchall()
            == []
        )
    finally:
        journal.close()
        store.close()


@pytest.mark.parametrize(
    "markers",
    [
        {"binding_digest": "a" * 64},
        {"binding_digest": None, "budget_digest": None},
        {"binding_digest": "a" * 64, "budget_digest": "invalid"},
        {"binding_digest": "a" * 64, "budget_digest": "b" * 64, "profile": "unrecognized"},
    ],
)
def test_child_dependency_decoder_rejects_partial_null_or_unknown_marker_fields(
    markers: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        ChildInvocationDependency.from_value(
            {"invocation_id": "legacy", "record_digest": "c" * 64, **markers}
        )
