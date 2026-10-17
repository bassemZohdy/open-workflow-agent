"""Behavioral edges for retained execution-authorization proof readers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from open_workflow_agent.effects import EffectIdentity, EffectJournal, EffectReceipt
from open_workflow_agent.errors import InvocationStateError, OwaError
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.execution_authorization import (
    AUTHORITY_OPERATION,
    _authority_descriptor,
    _authority_hint,
    _encoded,
    _parsed,
    _ProofInvocationReader,
    _table_exists,
    requires_authorization_seal,
    verify_authorized_business_snapshot,
    verify_execution_authorization_business_receipt,
)
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.security import Principal


def _business_identity(operation: str = "call:http") -> EffectIdentity:
    return EffectIdentity("invocation", "a" * 64, "/do/0/call", (("/do/0/call", 1),), operation)


def _assert_reason(error: InvocationStateError, reason: str) -> None:
    assert error.details == {"reason": reason}


def test_proof_json_codec_is_canonical_bounded_and_rejects_ambiguous_values() -> None:
    assert _encoded({"z": "é", "a": 1}) == '{"a":1,"z":"é"}'
    assert _parsed('{"a":1,"z":"é"}') == {"a": 1, "z": "é"}

    with pytest.raises(OwaError) as non_finite:
        _encoded({"number": float("nan")})
    assert non_finite.value.details["reason"] == "invalid_json"

    for value in ('{"a":1,"a":2}', "not-json"):
        with pytest.raises(InvocationStateError) as malformed:
            _parsed(value)
        _assert_reason(malformed.value, "authority_record_invalid")

    with pytest.raises(InvocationStateError) as too_large:
        _parsed('{"value":"long"}', limit=4)
    _assert_reason(too_large.value, "authority_limit")
    with pytest.raises(InvocationStateError) as oversized_encoding:
        _encoded({"value": "long"}, limit=4)
    _assert_reason(oversized_encoding.value, "authority_limit")


def test_authority_descriptors_and_operation_classification_fail_closed() -> None:
    authority = ExecutionAuthority.capture(
        Principal("alice"),
        security_profile="ingress",
    )
    assert _authority_descriptor(authority.as_value()) == authority
    with pytest.raises(InvocationStateError) as changed:
        _authority_descriptor({"profile": "unexpected"})
    _assert_reason(changed.value, "authority_descriptor_changed")

    assert requires_authorization_seal(_business_identity("call:http"))
    assert requires_authorization_seal(_business_identity("run:script"))
    for operation in ("emit", "listen"):
        assert requires_authorization_seal(_business_identity(operation))
    assert not requires_authorization_seal(_business_identity("agent:1.0.0@default"))


def test_authority_hint_finds_retained_authority_receipt_and_legacy_is_noop(
    tmp_path: Path,
) -> None:
    journal = EffectJournal(tmp_path / "effects.sqlite3")
    try:
        base = EffectIdentity("invocation", "a" * 64, "/do", (("/do", 1),), "legacy")
        assert _table_exists(journal.connection, "workflow_effects")
        assert not _table_exists(journal.connection, "table_that_does_not_exist")
        assert not _authority_hint(journal.connection, base)

        authority = EffectIdentity(
            base.invocation_id,
            base.workflow_fingerprint,
            base.task_reference,
            base.occurrence,
            AUTHORITY_OPERATION,
        )
        journal.connection.execute(
            "INSERT INTO workflow_effects(identity,owner,status,payload) VALUES (?,?,?,?)",
            (authority.key, "admission", "completed", "{}"),
        )
        assert _authority_hint(journal.connection, base)

        # A legacy, unprotected business result remains readable without turning
        # a missing authority table into an authorization failure.
        legacy = _business_identity("agent:1.0.0@default")
        verify_execution_authorization_business_receipt(
            journal.connection,
            InvocationStore(tmp_path / "legacy-invocations.sqlite3"),
            legacy,
            EffectReceipt("0" * 64, "legacy"),
        )
    finally:
        journal.close()


@pytest.mark.parametrize(
    "source",
    ["recovery-digest", "recovery-policy", "authority-row", "effect-index", "audit-row"],
)
def test_authority_hint_detects_each_retained_protection_marker(
    tmp_path: Path, source: str
) -> None:
    invocations = InvocationStore(tmp_path / f"{source}.sqlite3")
    connection = invocations.connection
    identity = EffectIdentity("marker", "a" * 64, "/do/0/call", (("/do/0/call", 1),), "call:http")
    try:
        if source.startswith("recovery-"):
            connection.execute(
                "CREATE TABLE recovery_invocation_definitions(invocation_id TEXT,payload TEXT)"
            )
            binding: dict[str, object] = {}
            if source == "recovery-digest":
                binding["execution_authority_digest"] = "b" * 64
            else:
                binding["runtime_profile"] = {
                    "policy": {"execution_authorization": {"enabled": True}}
                }
            connection.execute(
                "INSERT INTO recovery_invocation_definitions VALUES (?,?)",
                (identity.invocation_id, json.dumps(binding)),
            )
        elif source == "authority-row":
            connection.execute("CREATE TABLE execution_authorities(invocation_id TEXT)")
            connection.execute(
                "INSERT INTO execution_authorities VALUES (?)", (identity.invocation_id,)
            )
        elif source == "effect-index":
            connection.execute(
                "CREATE TABLE workflow_effect_invocations(invocation_id TEXT,payload TEXT)"
            )
            connection.execute(
                "INSERT INTO workflow_effect_invocations VALUES (?,?)",
                (identity.invocation_id, '{"operation":"policy:execution-authorization:v1"}'),
            )
        else:
            connection.execute("CREATE TABLE execution_authorization_decisions(invocation_id TEXT)")
            connection.execute(
                "INSERT INTO execution_authorization_decisions VALUES (?)",
                (identity.invocation_id,),
            )

        assert _authority_hint(connection, identity)
    finally:
        invocations.close()


def test_snapshot_verifier_is_noop_without_evidence_and_rejects_dangling_evidence(
    tmp_path: Path,
) -> None:
    journal = EffectJournal(tmp_path / "effects.sqlite3")
    try:
        # A pre-upgrade database has no authorization evidence to verify.
        legacy = EffectIdentity(
            "legacy", "d" * 64, "/do/0/agent", (("/do/0/agent", 1),), "agent:1.0.0@default"
        )
        verify_authorized_business_snapshot(
            journal.connection, legacy, EffectReceipt("0" * 64, "legacy")
        )
    finally:
        journal.close()

    journal = EffectJournal(tmp_path / "snapshot.sqlite3")
    try:
        dangling = EffectIdentity("dangling", "e" * 64, "/do", (("/do", 1),), "legacy")
        protected = EffectIdentity(
            dangling.invocation_id,
            dangling.workflow_fingerprint,
            dangling.task_reference,
            dangling.occurrence,
            AUTHORITY_OPERATION,
        )
        journal.connection.execute(
            "INSERT INTO workflow_effects(identity,owner,status,payload) VALUES (?,?,?,?)",
            (protected.key, "authority", "completed", "{}"),
        )
        with pytest.raises(InvocationStateError) as missing_tables:
            verify_authorized_business_snapshot(
                journal.connection, dangling, EffectReceipt("0" * 64, "legacy")
            )
        _assert_reason(missing_tables.value, "authority_binding_missing")
    finally:
        journal.close()


def test_business_proof_reader_preserves_legacy_results_but_rejects_dangling_audit(
    tmp_path: Path,
) -> None:
    invocations = InvocationStore(tmp_path / "legacy.sqlite3")
    business = _business_identity()
    receipt = EffectReceipt("0" * 64, "legacy", status="completed", output={"ok": True})
    try:
        # Older databases without authorization tables or markers keep their
        # historical receipt semantics.
        verify_execution_authorization_business_receipt(
            invocations.connection, invocations, business, receipt
        )

        invocations.connection.execute(
            "CREATE TABLE execution_authorization_outcomes(invocation_id TEXT)"
        )
        invocations.connection.execute(
            "INSERT INTO execution_authorization_outcomes VALUES (?)",
            (business.invocation_id,),
        )
        with pytest.raises(InvocationStateError) as dangling:
            verify_execution_authorization_business_receipt(
                invocations.connection, invocations, business, receipt
            )
        _assert_reason(dangling.value, "authority_binding_missing")
    finally:
        invocations.close()


def test_bounded_proof_reader_checks_persisted_identity_and_original_input(
    tmp_path: Path,
) -> None:
    invocations = InvocationStore(tmp_path / "invocations.sqlite3")
    try:
        handle = invocations.create(
            engine="test",
            session_id="proof-reader",
            user_id=None,
            workflow_name="proof-reader",
            workflow_version="1.0.0",
            workflow_fingerprint="1" * 64,
        )
        invocations.bind_input(handle, {"original": "request"})
        reader = _ProofInvocationReader(invocations.connection)
        loaded = reader.get(handle.invocation_id)
        assert loaded is not None
        assert loaded._persisted_payload == handle._persisted_payload
        assert reader.read_input(loaded) == {"original": "request"}

        invocations.connection.execute(
            "UPDATE invocation_inputs SET fingerprint=? WHERE invocation_id=?",
            ("2" * 64, handle.invocation_id),
        )
        with pytest.raises(InvocationStateError) as changed_input:
            reader.read_input(loaded)
        _assert_reason(changed_input.value, "authority_original_binding_changed")

        assert reader.get("absent") is None
    finally:
        invocations.close()
