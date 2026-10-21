"""Real hard-stop rollback inspection preserves originals and fails closed."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from open_workflow_agent import _native_sqlite_inspection as inspection
from open_workflow_agent.errors import InvocationStateError

_PRODUCER = r"""
import apsw,sys,time
from pathlib import Path
root=Path(sys.argv[1]); root.mkdir()
native=root/'native.sqlite3'; common=root/'runtime.sqlite3'
class CommitGateVFS(apsw.VFS):
 def __init__(self):
  super().__init__('hot-rollback-gate','')
 def xDelete(self,filename,syncdir):
  if Path(filename).name.startswith('native.sqlite3-mj'):
   (root/'barrier').write_text(filename)
   while True: time.sleep(1)
  return super().xDelete(filename,syncdir)
gate_vfs=CommitGateVFS()
db=apsw.Connection(str(native),vfs='hot-rollback-gate')
db.execute('PRAGMA page_size=4096')
db.execute('PRAGMA journal_mode=DELETE')
db.execute('PRAGMA synchronous=FULL')
db.execute('PRAGMA cache_size=-131072')
db.execute('PRAGMA cache_spill=OFF')
db.execute('ATTACH DATABASE ? AS common',(str(common),))
db.execute('PRAGMA common.journal_mode=DELETE')
db.execute('PRAGMA common.synchronous=FULL')
db.execute('PRAGMA common.cache_size=-131072')
db.execute('PRAGMA common.cache_spill=OFF')
for schema in ('main','common'):
 db.execute(f'CREATE TABLE {schema}.history(value TEXT)')
 db.execute(f'INSERT INTO {schema}.history VALUES(?)',('committed',))
 db.execute(f'CREATE TABLE {schema}.pages(value BLOB)')
 db.execute(f'INSERT INTO {schema}.pages VALUES(?)',(b'A'*(8*1024*1024),))
(root/'ready').write_text('ready')
db.execute('BEGIN IMMEDIATE')
for schema in ('main','common'):
 db.execute(f'UPDATE {schema}.history SET value=?',('uncommitted',))
 db.execute(f'UPDATE {schema}.pages SET value=?',(b'B'*(8*1024*1024),))
db.execute('COMMIT')
(root/'committed').write_text('committed')
time.sleep(30)
"""


def _fingerprints(directory):
    return {
        path.name: (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in directory.iterdir()
        if path.is_file()
    }


@pytest.fixture(scope="module")
def hot_rollback(tmp_path_factory):
    parent = tmp_path_factory.mktemp("native-hot-rollback")
    root = parent / "producer"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _PRODUCER, str(root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    found = False
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and process.poll() is None:
            if (root / "barrier").exists():
                master = next(root.glob("native.sqlite3-mj*"), None)
                journal = root / "native.sqlite3-journal"
                try:
                    with journal.open("rb") as stream:
                        stream.seek(-16, 2)
                        tail = stream.read(16)
                        stream.seek(8)
                        count = struct.unpack(">I", stream.read(4))[0]
                    if (
                        master is not None
                        and tail[-8:] == inspection._MAGIC
                        and 0 < count < 0xFFFFFFFF
                    ):
                        process.kill()
                        process.wait(timeout=5)
                        found = master.exists() and journal.exists()
                        break
                except (OSError, ValueError, struct.error):
                    pass
            time.sleep(0.01)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        error = process.stderr.read().decode(errors="replace")
        process.stderr.close()
    assert not error, error
    assert found, "real producer COMMIT did not reach the super-journal delete barrier"
    assert process.returncode != 0 and not (root / "committed").exists()
    return root


def _copy_fixture(source, target):
    target.mkdir()
    for path in source.iterdir():
        if path.is_file() and path.suffix == ".sqlite3":
            (target / path.name).write_bytes(path.read_bytes())
    original_master = next(source.glob("native.sqlite3-mj*"))
    master = target / original_master.name
    master.write_bytes(
        b"\0".join(
            str(target / name).encode()
            for name in ("native.sqlite3-journal", "runtime.sqlite3-journal")
        )
        + b"\0"
    )
    # Preserve actual SQLite-created journal records, rebinding only their
    # trusted master pathname for this isolated negative-test evidence set.
    name = str(master).encode()
    checksum = sum(value if value < 128 else value - 256 for value in name) & 0xFFFFFFFF
    for journal in [source / "native.sqlite3-journal"]:
        raw = journal.read_bytes()
        length = struct.unpack(">I", raw[-16:-12])[0]
        assert raw[-8:] == inspection._MAGIC
        (target / journal.name).write_bytes(
            raw[: -16 - length] + name + struct.pack(">II", len(name), checksum) + inspection._MAGIC
        )
    return target


def test_actual_hot_superjournal_inspection_rolls_back_only_private_copy(hot_rollback):
    native = hot_rollback / "native.sqlite3"
    before = _fingerprints(hot_rollback)
    with pytest.raises(sqlite3.OperationalError) as failure:
        with sqlite3.connect(native.as_uri() + "?mode=ro", uri=True) as db:
            db.execute("SELECT value FROM history").fetchall()
    assert failure.value.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK
    with inspection.inspect_native_sqlite(
        native, common_database=hot_rollback / "runtime.sqlite3"
    ) as db:
        assert db.execute("SELECT value FROM history").fetchall() == [("committed",)]
        copy = Path(db.execute("PRAGMA database_list").fetchone()[2]).parent
        assert copy != hot_rollback and copy.exists()
        assert not list(copy.glob("*mj*"))
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM history")
    assert not copy.exists() and _fingerprints(hot_rollback) == before


@pytest.mark.parametrize(
    "damage",
    [
        "wal",
        "unknown-master",
        "unknown-member",
        "checksum",
        "truncated",
        "page-checksum",
        "master-size",
        "snapshot-size",
    ],
)
def test_untrusted_or_corrupt_recovery_evidence_never_mutates_original(
    hot_rollback,
    tmp_path,
    monkeypatch,
    damage,
):
    root = _copy_fixture(hot_rollback, tmp_path / "evidence")
    native, journal = root / "native.sqlite3", root / "native.sqlite3-journal"
    master = next(root.glob("*mj*"))
    raw = bytearray(journal.read_bytes())
    if damage == "wal":
        Path(str(native) + "-wal").write_bytes(b"unqualified")
    elif damage == "unknown-master":
        length = struct.unpack(">I", raw[-16:-12])[0]
        name = str(tmp_path / "outside.sqlite3-mj000000900").encode()
        checksum = sum(value if value < 128 else value - 256 for value in name) & 0xFFFFFFFF
        journal.write_bytes(
            raw[: -16 - length] + name + struct.pack(">II", len(name), checksum) + inspection._MAGIC
        )
    elif damage == "unknown-member":
        master.write_bytes(str(tmp_path / "untrusted-journal").encode() + b"\0")
    elif damage == "checksum":
        raw[-9] ^= 1
        journal.write_bytes(raw)
    elif damage == "truncated":
        journal.write_bytes(raw[:-1])
    elif damage == "page-checksum":
        sector, page = struct.unpack(">II", raw[20:28])
        raw[sector + 4 + page - 200] ^= 1
        journal.write_bytes(raw)
    elif damage == "master-size":
        master.write_bytes(b"x" * (inspection.MAX_MASTER_BYTES + 1))
    else:
        monkeypatch.setattr(inspection, "MAX_SNAPSHOT_BYTES", 128)
    before = _fingerprints(root)
    # Corruption may make SQLite consider a journal cold. The bounded snapshot
    # validator itself must still reject, never supply a fabricated no-history.
    with pytest.raises((ValueError, InvocationStateError)):
        inspection._snapshot(native, root / "runtime.sqlite3")
    assert _fingerprints(root) == before


def test_missing_superjournal_uses_committed_native_bytes_without_original_cleanup(
    hot_rollback,
    tmp_path,
):
    root = _copy_fixture(hot_rollback, tmp_path / "evidence")
    native = root / "native.sqlite3"
    next(root.glob("*mj*")).unlink()
    before = _fingerprints(root)
    database, journal = inspection._snapshot(native, root / "runtime.sqlite3")
    assert database == native.read_bytes() and journal is None
    with inspection.inspect_native_sqlite(native, common_database=root / "runtime.sqlite3") as db:
        assert db.execute("PRAGMA schema_version").fetchone()[0] > 0
    assert _fingerprints(root) == before


def test_untrusted_unc_and_unknown_journal_names_reject_before_filesystem_lookup(
    hot_rollback, tmp_path, monkeypatch
):
    root = _copy_fixture(hot_rollback, tmp_path / "evidence")
    native, common = root / "native.sqlite3", root / "runtime.sqlite3"
    journal = Path(str(native) + "-journal")
    original = journal.read_bytes()
    length = struct.unpack(">I", original[-16:-12])[0]
    master = next(root.glob("*mj*"))
    unc = "//untrusted-owa-server/share/native.sqlite3-mj000000900"
    unknown = str(root / "untrusted.sqlite3-mj000000900")
    forbidden_keys = {inspection._key(Path(name)) for name in (unc, unknown)}
    original_stat, original_link = Path.stat, Path.is_symlink
    attempted = []

    def guarded_stat(path, *args, **kwargs):
        assert inspection._key(path) not in forbidden_keys, "untrusted path was stat'ed"
        attempted.append(("stat", path))
        return original_stat(path, *args, **kwargs)

    def guarded_link(path):
        assert inspection._key(path) not in forbidden_keys, "untrusted link was inspected"
        attempted.append(("is_symlink", path))
        return original_link(path)

    def forbidden_resolve(*args, **kwargs):
        pytest.fail("untrusted journal names must be compared without Path.resolve")

    for name in (unc, unknown, None):
        if name is None:
            journal.write_bytes(original)
            master.write_bytes(str(journal).encode() + b"\0" + unc.encode() + b"\0")
        else:
            encoded = name.encode()
            checksum = sum(value if value < 128 else value - 256 for value in encoded) & 0xFFFFFFFF
            journal.write_bytes(
                original[: -16 - length]
                + encoded
                + struct.pack(">II", len(encoded), checksum)
                + inspection._MAGIC
            )
        before = _fingerprints(root)
        with monkeypatch.context() as guarded:
            guarded.setattr(Path, "stat", guarded_stat)
            guarded.setattr(Path, "is_symlink", guarded_link)
            guarded.setattr(Path, "resolve", forbidden_resolve)
            with pytest.raises(ValueError, match="untrusted super-journal"):
                inspection._snapshot(native, common)
        assert _fingerprints(root) == before
    assert attempted and all(inspection._key(path) not in forbidden_keys for _, path in attempted)


def test_changing_snapshot_exhausts_only_bounded_consistency_retries(
    hot_rollback, tmp_path, monkeypatch
):
    root = _copy_fixture(hot_rollback, tmp_path / "evidence")
    native = root / "native.sqlite3"
    reader = inspection._read
    reads = []

    def changing(path, maximum):
        result = reader(path, maximum)
        if path.name.endswith("-journal"):
            reads.append(path)
            value = native.stat()
            os.utime(native, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(inspection, "_read", changing)
    with pytest.raises(InvocationStateError):
        with inspection.inspect_native_sqlite(native, common_database=root / "runtime.sqlite3"):
            pytest.fail("changing original evidence must not yield a connection")
    assert len(reads) == inspection._ATTEMPTS


def test_normal_readonly_inspection_does_not_create_a_copy(tmp_path, monkeypatch):
    native, common = tmp_path / "native.sqlite3", tmp_path / "runtime.sqlite3"
    with sqlite3.connect(native) as db:
        db.execute("CREATE TABLE history(value TEXT)")
        db.execute("INSERT INTO history VALUES('committed')")
    before = _fingerprints(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary read-only storage does not require a snapshot")

    monkeypatch.setattr(inspection.tempfile, "TemporaryDirectory", forbidden)
    with inspection.inspect_native_sqlite(native, common_database=common) as db:
        assert db.execute("SELECT value FROM history").fetchall() == [("committed",)]
    assert _fingerprints(tmp_path) == before


def test_private_copy_cleanup_runs_when_consumer_rejects_history(hot_rollback):
    with pytest.raises(InvocationStateError, match="consumer rejected"):
        with inspection.inspect_native_sqlite(
            hot_rollback / "native.sqlite3", common_database=hot_rollback / "runtime.sqlite3"
        ) as db:
            copy = Path(db.execute("PRAGMA database_list").fetchone()[2]).parent
            raise InvocationStateError("consumer rejected")
    assert not copy.exists()
