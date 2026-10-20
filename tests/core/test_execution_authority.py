"""Strict nonsecret ingress claims and task-local isolation, without provider I/O."""

from __future__ import annotations

import asyncio
import copy

import pytest
from open_workflow_agent.effects import EffectIdentity, EffectJournal
from open_workflow_agent.errors import ToolError
from open_workflow_agent.execution_authority import (
    ExecutionAuthority,
    authority_scope,
    current_authority,
)
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.security import (
    ApiKeySecurityProfile,
    BearerSecurityProfile,
    ExecutionAuthorizationConfig,
    Principal,
    authenticate_static,
)
from pydantic import ValidationError


def _authority():
    return ExecutionAuthority.capture(
        Principal("alice", frozenset({"operator"}), frozenset({"read"}), frozenset({"runtime"})),
        security_profile="ingress",
    )


def test_original_authority_roundtrip_contains_all_claims_and_no_credential_reference():
    value = _authority().as_value()
    assert ExecutionAuthority.from_value(value) == _authority()
    assert value["principal"] == {
        "identity": "alice",
        "roles": ["operator"],
        "scopes": ["read"],
        "audience": ["runtime"],
    }
    assert "token" not in str(value) and "from_env" not in str(value)
    value["principal"]["roles"].append("admin")
    assert _authority().principal.roles == frozenset({"operator"})


@pytest.mark.parametrize(
    "mutation",
    ["extra", "profile", "duplicates", "unsorted", "control", "surrogate", "oversize", "label"],
)
def test_untrusted_descriptor_rejects_extra_or_unbounded_claims(mutation):
    value = copy.deepcopy(_authority().as_value())
    if mutation == "extra":
        value["token"] = "untrusted"
    elif mutation == "profile":
        value["profile"] = "unknown"
    elif mutation == "duplicates":
        value["principal"]["roles"] = ["admin", "admin"]
    elif mutation == "unsorted":
        value["principal"]["scopes"] = ["z", "a"]
    elif mutation == "control":
        value["principal"]["identity"] = "alice\nadmin"
    elif mutation == "surrogate":
        value["principal"]["identity"] = "alice\ud800"
    elif mutation == "oversize":
        value["principal"]["audience"] = ["x" * 257]
    else:
        value["security_profile"] = " ingress"
    with pytest.raises(ValueError):
        ExecutionAuthority.from_value(value)


@pytest.mark.parametrize(
    "field",
    ["identity", "role", "scope", "audience", "security_profile"],
)
def test_unpaired_surrogates_are_rejected_as_invalid_authority_references(field):
    selected = _authority().as_value()
    if field == "identity":
        selected["principal"]["identity"] = "alice\ud800"
    elif field == "security_profile":
        selected["security_profile"] = "ingress\ud800"
    else:
        selected["principal"][
            {"role": "roles", "scope": "scopes", "audience": "audience"}[field]
        ] = ["reader\ud800"]
    with pytest.raises(ValueError):
        ExecutionAuthority.from_value(selected)


@pytest.mark.asyncio
async def test_parallel_authority_scopes_restore_after_success_and_exception():
    ready = asyncio.Event()
    observed = []

    async def worker(identity):
        selected = ExecutionAuthority(Principal(identity), "ingress")
        with authority_scope(selected):
            await ready.wait()
            observed.append(current_authority())
            with authority_scope(None):
                assert current_authority() is None
            assert current_authority() == selected
        assert current_authority() is None

    tasks = [asyncio.create_task(worker(name)) for name in ("alice", "bob")]
    ready.set()
    await asyncio.gather(*tasks)
    assert {value.principal.identity for value in observed} == {"alice", "bob"}
    with pytest.raises(RuntimeError), authority_scope(_authority()):
        raise RuntimeError("fixture")
    assert current_authority() is None


@pytest.mark.parametrize("kind", ["bearer", "api_key"])
def test_actual_selected_static_profile_issues_exact_principal(monkeypatch, kind):
    monkeypatch.setenv("OWA_AUTHORITY_CANARY", "graphic.canary")
    common = {
        "principal": "alice",
        "roles": ["operator"],
        "scopes": ["read"],
        "audience": ["runtime"],
    }
    if kind == "bearer":
        profile = BearerSecurityProfile(token={"from_env": "OWA_AUTHORITY_CANARY"}, **common)
        headers = [(b"Authorization", b"bEaReR graphic.canary")]
    else:
        profile = ApiKeySecurityProfile(key={"from_env": "OWA_AUTHORITY_CANARY"}, **common)
        headers = [(b"x-api-key", b"graphic.canary")]
    assert authenticate_static(headers, profile) == _authority().principal


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer canary"), (b"AUTHORIZATION", b"Bearer canary")],
        [(b"authorization", b"canary")],
        [(b"authorization", b"Bearer  canary")],
        [(b"authorization", b"Bearer canary\n")],
        [(b"authorization", b"Bearer \xff")],
        [(b"authorization", b"Bearer canary"), (b"Authorization ", b"Bearer canary")],
        [(b"authorization", b"Bearer " + b"x" * 8192)],
    ],
    ids=[
        "missing",
        "duplicate",
        "scheme",
        "space",
        "control",
        "nonascii",
        "header-name",
        "oversize",
    ],
)
def test_ambiguous_raw_credentials_fail_sanitized_before_identity(monkeypatch, headers):
    monkeypatch.setenv("OWA_AUTHORITY_CANARY", "canary")
    profile = BearerSecurityProfile(token={"from_env": "OWA_AUTHORITY_CANARY"}, principal="alice")
    with pytest.raises(ToolError) as caught:
        authenticate_static(headers, profile)
    assert "canary" not in str(caught.value) and "canary" not in str(caught.value.details)


@pytest.mark.parametrize(
    "change",
    [
        {"id": "policy\n"},
        {"max_business_receipt_bytes": True},
        {"max_business_receipt_bytes": 67_108_865},
        {"policy": {"rules": [{"actions": ["x" * 4097]}]}},
    ],
    ids=["reference", "bool-limit", "output-limit", "rule-limit"],
)
def test_deployment_policy_rejects_unbounded_or_malformed_declarations(change):
    with pytest.raises(ValidationError):
        ExecutionAuthorizationConfig.model_validate(change)


@pytest.mark.parametrize(
    "with_invocations", [False, True], ids=["journal-only", "plain-invocations"]
)
def test_plain_standalone_legacy_effect_journal_requires_no_authority_bootstrap(
    tmp_path, with_invocations
):
    path = tmp_path / "legacy.sqlite3"
    invocations = InvocationStore(path) if with_invocations else None
    journal = EffectJournal(path, invocations=invocations)
    identity = EffectIdentity("legacy", "a" * 64, "/do/0/http", (("/do/0/http", 1),), "call:http")
    try:
        journal.begin(identity, "b" * 64, "first")
        receipt = journal.finish(identity, "first", output=None)
        assert journal.get(identity) == receipt and receipt.status == "completed"
        names = {
            row[0]
            for row in journal.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "execution_authorities" not in names
        assert ("invocations" in names) is with_invocations
    finally:
        journal.close()
        if invocations is not None:
            invocations.close()
