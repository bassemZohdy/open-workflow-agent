"""Fail-closed edge cases for immutable recovery-definition evidence."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from open_workflow_agent import recovery_definitions as definitions
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.workflow import compile_workflow


def _unavailable(callable_, reason: str) -> None:
    with pytest.raises(InvocationStateError) as caught:
        callable_()
    assert caught.value.details == {"reason": reason}


def test_archive_codec_rejects_duplicate_keys_and_byte_limit() -> None:
    with pytest.raises(ValueError, match="duplicate field"):
        definitions._parsed('{"record":1,"record":2}')

    _unavailable(lambda: definitions._parsed("{}", limit=1), "archive_limit_exceeded")
    _unavailable(lambda: definitions._encoded({"value": 2}, limit=1), "archive_limit_exceeded")


def test_distribution_record_inventory_is_digest_bound_unique_and_sorted() -> None:
    valid = b"z.py,,\na.py,,\n"
    digest = hashlib.sha256(valid).hexdigest()
    assert definitions._record_paths(digest, "metadata", valid) == ("a.py", "z.py")

    _unavailable(
        lambda: definitions._record_paths("0" * 64, "metadata", valid),
        "runtime_profile_unavailable",
    )
    duplicate = b"same.py,,\nsame.py,,\n"
    _unavailable(
        lambda: definitions._record_paths(
            hashlib.sha256(duplicate).hexdigest(), "metadata", duplicate
        ),
        "runtime_profile_unavailable",
    )
    malformed = b"path.py\n"
    _unavailable(
        lambda: definitions._record_paths(
            hashlib.sha256(malformed).hexdigest(), "metadata", malformed
        ),
        "runtime_profile_unavailable",
    )


def test_distribution_record_inventory_enforces_row_limit() -> None:
    rows = b"".join(f"module-{index:05}.py,,\n".encode() for index in range(65_537))
    _unavailable(
        lambda: definitions._record_paths(hashlib.sha256(rows).hexdigest(), "metadata", rows),
        "runtime_profile_unavailable",
    )


def test_custom_distribution_without_inventory_fails_closed() -> None:
    distribution = SimpleNamespace(files=None)
    _unavailable(lambda: definitions._sdk_paths(distribution), "runtime_profile_unavailable")


def test_sdk_hash_rejects_changed_file_and_empty_code_inventory(tmp_path, monkeypatch) -> None:
    definitions._sdk_file_digest.cache_clear()
    monkeypatch.setattr(definitions, "_file_bytes", lambda path, limit: b"x")
    _unavailable(
        lambda: definitions._sdk_file_digest((("module.py", str(tmp_path / "module.py"), 1, 2),)),
        "runtime_profile_unavailable",
    )

    distribution = SimpleNamespace(
        files=("metadata.txt",), locate_file=lambda item: tmp_path / item
    )
    monkeypatch.setattr(definitions.importlib.metadata, "distribution", lambda name: distribution)
    _unavailable(lambda: definitions._sdk_digest("custom-sdk"), "runtime_profile_unavailable")


def test_sdk_hash_rejects_non_regular_code_artifact(tmp_path, monkeypatch) -> None:
    distribution = SimpleNamespace(files=("package/module.py",), locate_file=lambda item: tmp_path)
    monkeypatch.setattr(definitions.importlib.metadata, "distribution", lambda name: distribution)
    _unavailable(lambda: definitions._sdk_digest("custom-sdk"), "runtime_profile_unavailable")


def test_recovery_profile_rejects_unknown_backend_before_metadata_lookup() -> None:
    _unavailable(
        lambda: definitions.recovery_profile("reference", backend="memory"),
        "runtime_profile_unavailable",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda archive: archive.update(profile="unexpected"),
        lambda archive: archive.update(imports=[{"reference": "bad"}]),
        lambda archive: archive.update(resources=[{"uri": "bad"}]),
        lambda archive: archive.update(script_resources={"unexpected": True}),
    ],
)
def test_malformed_archived_definition_is_never_normalized(mutate) -> None:
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "edge-case",
                "version": "1.0.0",
            },
            "do": [{"pause": {"wait": {"seconds": 60}}}],
        }
    )
    archive = definitions._definition(plan)
    mutate(archive)
    _unavailable(lambda: definitions._plan(archive), "archive_invalid")


def test_archived_definition_rejects_excessive_collections_and_fingerprint_drift() -> None:
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "collection-limit",
                "version": "1.0.0",
            },
            "do": [{"pause": {"wait": {"seconds": 60}}}],
        }
    )
    archive = definitions._definition(plan)
    archive["imports"] = [None] * (definitions.MAX_DEPENDENCIES + 1)
    _unavailable(lambda: definitions._plan(archive), "archive_invalid")

    archive = definitions._definition(plan)
    archive["fingerprint"] = "0" * 64
    _unavailable(lambda: definitions._plan(archive), "archive_invalid")
