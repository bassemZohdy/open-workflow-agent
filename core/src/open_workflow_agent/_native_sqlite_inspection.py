"""Consistent native SQLite reads without recovering the original database.

Rollback formats follow SQLite's pager.c read/writeSuperJournal and pager_cksum.
Only a stable, bounded, trusted per-file rollback is recovered in a private copy.
This is inspection, never a checkpoint transplant or permission to resume.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import struct
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from .errors import InvocationStateError

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_MASTER_BYTES = 64 * 1024
_MAGIC = bytes.fromhex("d9d505f920a163d7")
_ATTEMPTS = 3


class _Changed(Exception):
    pass


class _Signature(NamedTuple):
    device: int
    inode: int
    size: int
    modified: int
    changed: int


def _signature(value: os.stat_result) -> _Signature:
    return _Signature(
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    )


def _read(path: Path, maximum: int) -> tuple[bytes, _Signature]:
    if path.is_symlink():
        raise ValueError("untrusted snapshot link")
    named_before = _signature(path.stat())
    with path.open("rb") as stream:
        initial = os.fstat(stream.fileno())
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > maximum:
            raise ValueError("native snapshot exceeds its finite bound")
        raw = stream.read(initial.st_size + 1)
        if len(raw) != initial.st_size or _signature(os.fstat(stream.fileno())) != _signature(
            initial
        ):
            raise _Changed
    named_after = _signature(path.stat())
    opened = _signature(initial)
    if named_after != named_before or opened[:4] != named_before[:4]:
        raise _Changed
    # Windows stat reports creation time where fstat reports change time.
    # Each API retains its own complete before/after comparison; cross-API
    # device/inode/size/mtime still identify the same opened pathname.
    if os.name != "nt" and opened.changed != named_before.changed:
        raise _Changed
    return raw, named_before


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _journal_prefix(raw: bytes) -> tuple[bytes, str | None]:
    """Reject truncated/corrupt headers/pages instead of accepting partial undo."""
    if len(raw) < 28 or raw[:8] != _MAGIC:
        raise ValueError("unsupported hot journal header")
    master_name = None
    if raw[-8:] == _MAGIC:
        length, checksum = struct.unpack(">II", raw[-16:-8])
        if not 0 < length <= 4096 or length + 20 >= len(raw):
            raise ValueError("invalid master trailer length")
        name = raw[-16 - length : -16]
        if (
            b"\0" in name
            or (sum(value if value < 128 else value - 256 for value in name) & 0xFFFFFFFF)
            != checksum
        ):
            raise ValueError("invalid master trailer checksum")
        master_name = name.decode("utf-8", errors="strict")
        page_size = struct.unpack(">I", raw[24:28])[0]
        sentinel = struct.unpack(">I", raw[-20 - length : -16 - length])[0]
        if page_size == 0 or sentinel != 0x40000000 // page_size + 1:
            raise ValueError("invalid master pointer record")
        raw = raw[: -20 - length]
    offset = 0
    while offset < len(raw):
        if len(raw) - offset < 28 or raw[offset : offset + 8] != _MAGIC:
            raise ValueError("truncated journal header")
        count, nonce, _, sector, page = struct.unpack(">IIIII", raw[offset + 8 : offset + 28])
        if (
            not 0 < count < 0xFFFFFFFF
            or not 512 <= sector <= 65536
            or sector & (sector - 1)
            or not 512 <= page <= 65536
            or page & (page - 1)
        ):
            raise ValueError("unqualified journal format")
        position = offset + sector
        end = position + count * (page + 8)
        if end > len(raw):
            raise ValueError("truncated journal pages")
        for _ in range(count):
            number = struct.unpack(">I", raw[position : position + 4])[0]
            data = raw[position + 4 : position + 4 + page]
            checksum = struct.unpack(">I", raw[position + 4 + page : position + 8 + page])[0]
            if (
                number in {0, 0x40000000 // page + 1}
                or checksum
                != (nonce + sum(data[index] for index in range(page - 200, 0, -200))) & 0xFFFFFFFF
            ):
                raise ValueError("corrupt journal page")
            position += page + 8
        aligned = ((end + sector - 1) // sector) * sector
        if any(raw[end : min(aligned, len(raw))]):
            raise ValueError("invalid journal alignment")
        if aligned >= len(raw):
            return raw, master_name
        offset = aligned
    raise ValueError("empty journal")


def _master(
    name: str,
    native: Path,
    common: Path,
) -> tuple[Path, bytes | None, _Signature | None]:
    master = Path(name)
    if not master.is_absolute() or master.is_symlink():
        raise ValueError("untrusted super-journal path")
    allowed = (native, common)
    if not any(
        _key(master.parent) == _key(database.parent)
        and re.fullmatch(
            re.escape(database.name) + r"-mj[0-9a-fA-F]{6}9[0-9a-fA-F]{2}", master.name
        )
        for database in allowed
    ):
        raise ValueError("untrusted super-journal name")
    if not master.exists():
        return master, None, None
    content, signature = _read(master, MAX_MASTER_BYTES)
    entries = content.split(b"\0")
    if not content or entries[-1] != b"" or not 1 <= len(entries) - 1 <= 2:
        raise ValueError("invalid super-journal members")
    members = [Path(entry.decode("utf-8", errors="strict")) for entry in entries[:-1]]
    trusted = {_key(Path(str(database) + "-journal")) for database in allowed}
    if (
        any(not member.is_absolute() for member in members)
        or len({_key(member) for member in members}) != len(members)
        or not {_key(member) for member in members} <= trusted
        or _key(Path(str(native) + "-journal")) not in {_key(member) for member in members}
    ):
        raise ValueError("untrusted super-journal members")
    return master, content, signature


def _snapshot(native: Path, common: Path) -> tuple[bytes, bytes | None]:
    journal = Path(str(native) + "-journal")
    for suffix in ("-wal", "-shm"):
        if Path(str(native) + suffix).exists():
            raise ValueError("WAL recovery is outside this inspection profile")
    database, db_signature = _read(native, MAX_SNAPSHOT_BYTES)
    if (
        len(database) < 100
        or database[:16] != b"SQLite format 3\0"
        or database[18:20] != b"\x01\x01"
    ):
        raise ValueError("unsupported native database format")
    raw, journal_signature = _read(journal, MAX_SNAPSHOT_BYTES - len(database))
    prefix, master_name = _journal_prefix(raw)
    rollback: bytes | None = prefix
    master = None
    master_signature = None
    if master_name is not None:
        master, _, master_signature = _master(master_name, native, common)
        if master_signature is None:
            # SQLite considers this journal cold after the commit decision.
            rollback = None
    if _signature(native.stat()) != db_signature or _signature(journal.stat()) != journal_signature:
        raise _Changed
    if master is not None:
        if master_signature is None:
            if master.exists():
                raise _Changed
        elif not master.exists() or _signature(master.stat()) != master_signature:
            raise _Changed
    for suffix in ("-wal", "-shm"):
        if Path(str(native) + suffix).exists():
            raise _Changed
    return database, rollback


@contextmanager
def inspect_native_sqlite(path: Path, *, common_database: Path) -> Iterator[sqlite3.Connection]:
    """Yield a consistent query-only connection, never write the original file."""
    database = path.resolve()
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            db.execute("PRAGMA schema_version").fetchone()
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_READONLY_ROLLBACK:
                raise
            db.close()
            db = None
        if db is not None:
            db.execute("PRAGMA query_only=ON")
            yield db
            return
        for attempt in range(_ATTEMPTS):
            try:
                content, journal = _snapshot(database, common_database.resolve())
                break
            except _Changed:
                if attempt + 1 == _ATTEMPTS:
                    raise
        with tempfile.TemporaryDirectory(prefix="owa-native-inspection-") as directory:
            copy = Path(directory) / "native.sqlite3"
            copy.write_bytes(content)
            if journal is not None:
                Path(str(copy) + "-journal").write_bytes(journal)
            db = sqlite3.connect(copy, timeout=1)
            try:
                db.execute("PRAGMA schema_version").fetchone()
                db.execute("PRAGMA query_only=ON")
                yield db
            finally:
                db.close()
                db = None
    except InvocationStateError:
        raise
    except Exception:
        raise InvocationStateError(
            "native initial storage could not be consistently inspected"
        ) from None
    finally:
        if db is not None:
            db.close()
