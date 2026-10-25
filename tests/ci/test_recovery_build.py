"""Publication rejects missing, malformed or mismatched recovery provenance."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from ci.recovery_build import verify_wheel


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    metadata = (
        b'[project]\nname = "test-runtime"\nversion = "1.0.0"\n'
        b'[tool.hatch.build.targets.wheel]\npackages = ["src/test_runtime"]\n'
    )
    lock = b"version = 1\nrevision = 3\n"
    (tmp_path / "pyproject.toml").write_bytes(metadata)
    (tmp_path / "uv.lock").write_bytes(lock)
    return tmp_path, {
        "profile": "owa/recovery-build/v1",
        "project": "test-runtime",
        "version": "1.0.0",
        "lock_sha256": hashlib.sha256(lock).hexdigest(),
        "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
    }


@pytest.mark.parametrize("damage", ["missing", "invalid", "lock", "version", "oversized"])
def test_wheel_with_unverifiable_recovery_identity_cannot_pass_publication(
    project: tuple[Path, dict[str, str]], damage: str
) -> None:
    directory, record = project
    wheel = directory / "runtime.whl"
    if damage == "lock":
        record["lock_sha256"] = "0" * 64
    elif damage == "version":
        record["version"] = "2.0.0"
    content = json.dumps(record) if damage != "invalid" else "{"
    if damage == "oversized":
        content += " " * 4096
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("test_runtime/__init__.py", "")
        if damage != "missing":
            archive.writestr("test_runtime/_recovery_build.json", content)
    with pytest.raises(ValueError):
        verify_wheel(wheel, directory)


def test_build_identity_matches_canonical_lock_content_across_checkout_newlines(
    project: tuple[Path, dict[str, str]],
) -> None:
    directory, record = project
    wheel = directory / "runtime.whl"
    for name in ("uv.lock", "pyproject.toml"):
        path = directory / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("test_runtime/_recovery_build.json", json.dumps(record))
        archive.writestr("test_runtime/__init__.py", "")
    result = verify_wheel(wheel, directory)
    assert result["lock_sha256"] == record["lock_sha256"]
    assert result["metadata_sha256"] == record["metadata_sha256"]
    assert result["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_valid_build_identity_cannot_qualify_a_metadata_only_wheel(
    project: tuple[Path, dict[str, str]],
) -> None:
    directory, record = project
    wheel = directory / "runtime.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("test_runtime/_recovery_build.json", json.dumps(record))
    with pytest.raises(ValueError, match="wheel package initialization is missing"):
        verify_wheel(wheel, directory)
