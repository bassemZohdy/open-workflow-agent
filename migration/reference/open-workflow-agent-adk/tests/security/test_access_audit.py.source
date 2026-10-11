import pytest

from openworkflow_adk import AccessPolicy, AuditLog, AuthorizationError, Principal


def test_access_policy_enforces_management_permissions() -> None:
    policy = AccessPolicy({"operator": {"workflow:run"}})
    principal = Principal("alice", frozenset({"operator"}))
    policy.require(principal, "workflow:run")
    with pytest.raises(AuthorizationError):
        policy.require(principal, "workflow:delete")


def test_audit_log_hash_chain_detects_tampering() -> None:
    audit = AuditLog()
    audit.append("alice", "workflow.run", "demo/job")
    audit.append("alice", "run.inspect", "run-1")
    assert audit.verify()
    audit.entries[0] = audit.entries[0].__class__(
        **{**audit.entries[0].__dict__, "action": "workflow.delete"}
    )
    assert not audit.verify()


def test_audit_log_hash_chain_detects_deletion() -> None:
    audit = AuditLog()
    audit.append("alice", "workflow.run", "demo/job")
    audit.append("alice", "run.inspect", "run-1")
    del audit.entries[1]
    assert not audit.verify()
