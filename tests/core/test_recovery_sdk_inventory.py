"""Installed inventory caching retains fresh physical continuation evidence."""

import importlib.metadata

import open_workflow_agent.recovery_definitions as recovery
import pytest
from open_workflow_agent.errors import InvocationStateError


def installed(tmp_path, monkeypatch):
    metadata = tmp_path / "checkpoint-1.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: checkpoint\nVersion: 1.0.0\n")
    record = metadata / "RECORD"
    record.write_text("codec.py,,\ncheckpoint-1.0.0.dist-info/RECORD,,\n")
    source = tmp_path / "codec.py"
    source.write_text("# immutable serializer\n")
    selected = importlib.metadata.PathDistribution(metadata)
    monkeypatch.setattr(recovery.importlib.metadata, "distribution", lambda name: selected)
    recovery._record_paths.cache_clear()
    return record, source


def test_same_record_reuses_only_enumeration_and_freshly_stats_code(tmp_path, monkeypatch):
    _record, source = installed(tmp_path, monkeypatch)
    first = recovery._sdk_digest("checkpoint")
    original_stat = type(source).stat
    observed = []

    def observe(path, *args, **kwargs):
        if path == source:
            observed.append(True)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(source), "stat", observe)
    assert recovery._sdk_digest("checkpoint") == first
    assert observed and recovery._record_paths.cache_info().hits >= 1
    source.write_text("# changed serializer under the same installed package version\n")
    assert recovery._sdk_digest("checkpoint") != first


@pytest.mark.parametrize(
    "change", ["added", "deleted", "changed-record", "missing-record", "oversize-record"]
)
def test_installed_inventory_and_file_changes_never_use_stale_profile(
    tmp_path, monkeypatch, change
):
    record, source = installed(tmp_path, monkeypatch)
    first = recovery._sdk_digest("checkpoint")
    if change == "added":
        (tmp_path / "other.py").write_text("# new checkpoint code\n")
        record.write_text(record.read_text() + "other.py,,\n")
        assert recovery._sdk_digest("checkpoint") != first
    else:
        if change == "deleted":
            source.unlink()
        elif change == "changed-record":
            record.write_text("absent-code.py,,\n")
        elif change == "missing-record":
            record.unlink()
        else:
            record.write_bytes(b"x" * 4_000_001)
        with pytest.raises(InvocationStateError) as failure:
            recovery._sdk_digest("checkpoint")
        assert failure.value.details == {"reason": "runtime_profile_unavailable"}


def test_independent_installations_with_identical_record_paths_have_distinct_physical_code(
    tmp_path, monkeypatch
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    installed(first_root, monkeypatch)
    first = recovery._sdk_digest("checkpoint")
    _record, source = installed(second_root, monkeypatch)
    source.write_text("# other installation code\n")
    assert recovery._sdk_digest("checkpoint") != first
