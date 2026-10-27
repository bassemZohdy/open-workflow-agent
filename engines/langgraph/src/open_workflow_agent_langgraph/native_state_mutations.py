"""Private atomic SDK updater primitive under the installed control boundary.

Callers must authorize and freeze a validated update before entering this boundary.
The lease must exclude workflow execution; it cannot be shared with a live runner.
Only SDK/storage work may run inside the callback: common synchronous persistence
must run before or after it, so ownership locks cannot invert across event loops.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import UUID

from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError, OwaError
from open_workflow_agent.ownership import (
    NativeWriteFence,
    install_sqlite_native_fence,
    run_native_storage_write,
    translate_native_fence_error,
)
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.workflow import WorkflowPlan

from .graph import LangGraphScopeExecutor

TABLE = "owa_state_updates_v1"
_DIGEST = re.compile(r"[a-f0-9]{64}")
_DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} ("
    "identity TEXT PRIMARY KEY, invocation_id TEXT NOT NULL, fingerprint TEXT NOT NULL, "
    "request_digest TEXT NOT NULL, base_checkpoint TEXT NOT NULL, result_checkpoint TEXT)"
)


def _checkpoint(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError("native checkpoint identity is invalid")
    return value


@dataclass(frozen=True, slots=True)
class NativeStateWrite:
    identity: str
    request_digest: str
    base_checkpoint: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not _DIGEST.fullmatch(value)
            for value in (self.identity, self.request_digest)
        ):
            raise ValueError("native state write identity is invalid")
        _checkpoint(self.base_checkpoint)


@dataclass(frozen=True, slots=True)
class NativeStateWriteProof:
    request: NativeStateWrite
    result_checkpoint: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, NativeStateWrite):
            raise ValueError("native state write proof is invalid")
        _checkpoint(self.result_checkpoint)
        if self.result_checkpoint == self.request.base_checkpoint:
            raise ValueError("native state write must create a new checkpoint")


_INDEX = f"CREATE INDEX IF NOT EXISTS owa_state_updates_invocation_v1 ON {TABLE}(invocation_id)"


async def prepare_state_update_storage(saver: Any, *, postgresql: bool) -> None:
    """Deployment setup only, after the native saver has been prepared."""
    if postgresql:
        async with saver.conn.transaction():
            await saver.conn.execute("SELECT pg_advisory_xact_lock(%s,%s)", (0x4F5741, 0x53553031))
            await saver.conn.execute(_DDL)
            await saver.conn.execute(_INDEX)
    else:
        await saver.conn.execute(_DDL)
        await saver.conn.execute(_INDEX)
        await saver.conn.commit()


class _TransactionConnection:
    """Keep the pinned SQLite SDK's individual commits inside our outer unit."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    async def commit(self) -> None:
        pass


@asynccontextmanager
async def _saver(
    database: str,
    fence: NativeWriteFence,
    *,
    proof_table: str = TABLE,
) -> AsyncIterator[tuple[Any, Any, bool]]:
    postgres = is_postgresql_datasource(database)
    if postgres:
        sdk = import_module("langgraph.checkpoint.postgres.aio").AsyncPostgresSaver

        class AtomicSaver(sdk):  # type: ignore[misc,valid-type]
            @asynccontextmanager
            async def _cursor(self, *, pipeline: bool = False) -> AsyncIterator[Any]:
                # Every SDK read/write stays on the one owned transaction.
                async with self.lock, self.conn.cursor() as cursor:
                    yield cursor

        conninfo = import_module("psycopg.conninfo").make_conninfo(database, connect_timeout=5)
        async with AtomicSaver.from_conn_string(conninfo) as saver:
            async with saver.conn.transaction():
                await saver.conn.execute("SET LOCAL statement_timeout=5000")
                await saver.conn.execute("SET LOCAL lock_timeout=5000")
                yield saver, saver.conn, True
        return
    if database == ":memory:" or not Path(database).is_file():
        raise InvocationStateError("native state update storage is absent")
    sdk = import_module("langgraph.checkpoint.sqlite.aio").AsyncSqliteSaver
    aiosqlite = import_module("aiosqlite")
    async with aiosqlite.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        # No setup DDL is allowed in the mutation path, including on a missing
        # proof table. Installing connection-local fence triggers must succeed.
        await install_sqlite_native_fence(
            conn, fence, native_database=database, tables=("checkpoints", "writes", proof_table)
        )
        saver = sdk(_TransactionConnection(conn))
        saver.is_setup = True
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield saver, conn, False
            await conn.commit()
        except BaseException as error:
            await conn.rollback()
            translate_native_fence_error(error)
            raise


async def _guard(connection: Any, fence: NativeWriteFence) -> None:
    statement, values = fence.postgresql_guard()
    # A whole state update must serialize its head comparison with other state
    # updates and all ordinary fenced SDK commits, including the same owner.
    # Ordinary execution retains its existing shared-fence contract.
    if not statement.endswith(" FOR SHARE"):
        raise InvocationStateError("native state update fence is unsupported")
    statement = statement.removesuffix(" FOR SHARE") + " FOR UPDATE"
    async with connection.cursor() as cursor:
        await cursor.execute(statement.replace("?", "%s"), values)
        if await cursor.fetchone() is None:
            raise InvocationStateConflict("native state update ownership was lost")


async def apply_native_state_update(
    database: str,
    fence: NativeWriteFence,
    handle: ExecutionHandle,
    plan: WorkflowPlan,
    request: NativeStateWrite,
    update: Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> NativeStateWriteProof:
    """CAS the root head, SDK checkpoint/writes and native proof in one commit.

    This does not create a common control intent, authorize a management request,
    validate a workflow update policy, or make a checkpoint safe to resume.
    """
    if (
        handle.engine != "langgraph"
        or fence.lease.engine != "langgraph"
        or fence.lease.invocation_id != handle.invocation_id
        or fence.lease.workflow_fingerprint != plan.fingerprint
        or handle.workflow_fingerprint != plan.fingerprint
    ):
        raise InvocationStateConflict("native state write ownership binding is inconsistent")
    config = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)
    thread = config["configurable"]["thread_id"]

    async def write() -> NativeStateWriteProof:
        async with asyncio.timeout(15), _saver(database, fence) as (saver, conn, postgres):
            placeholder = "%s" if postgres else "?"
            if postgres:
                await _guard(conn, fence)
            async with conn.cursor() as cursor:
                if not postgres:
                    # A repeated request still needs a live fence. The trigger
                    # also locks common ownership before returning old proof.
                    await cursor.execute(
                        f"UPDATE {TABLE} SET request_digest=request_digest WHERE identity=?",
                        (request.identity,),
                    )
                size = "octet_length" if postgres else "length"
                fields = (
                    ("invocation_id", 256),
                    ("fingerprint", 256),
                    ("request_digest", 64),
                    ("base_checkpoint", 36),
                    ("result_checkpoint", 36),
                )
                await cursor.execute(
                    "SELECT "
                    + ",".join(
                        f"CASE WHEN {size}({name})<={bound} THEN {name} ELSE NULL END AS {name}"
                        for name, bound in fields
                    )
                    + f" FROM {TABLE} WHERE identity={placeholder}",
                    (request.identity,),
                )
                row = await cursor.fetchone()
                if row is not None:
                    values = tuple(row.values()) if postgres else tuple(row)
                    if values[:4] != (
                        handle.invocation_id,
                        plan.fingerprint,
                        request.request_digest,
                        request.base_checkpoint,
                    ):
                        raise InvocationStateConflict("native state update request changed")
                    proof = NativeStateWriteProof(request, values[4])
                    await cursor.execute(
                        "SELECT parent_checkpoint_id FROM checkpoints WHERE thread_id="
                        + placeholder
                        + " AND checkpoint_ns='' AND checkpoint_id="
                        + placeholder,
                        (thread, proof.result_checkpoint),
                    )
                    retained = await cursor.fetchone()
                    parent = (
                        retained["parent_checkpoint_id"]
                        if postgres and retained
                        else retained[0]
                        if retained
                        else None
                    )
                    if parent != request.base_checkpoint:
                        raise InvocationStateConflict("native state update proof is inconsistent")
                    return proof
                await cursor.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id="
                    + placeholder
                    + " AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread,),
                )
                row = await cursor.fetchone()
                current = row["checkpoint_id"] if postgres and row else row[0] if row else None
                if current != request.base_checkpoint:
                    raise InvocationStateConflict("native state update base is stale")
                await cursor.execute(
                    f"SELECT COUNT(*) AS count FROM {TABLE} WHERE invocation_id={placeholder}",
                    (handle.invocation_id,),
                )
                count = await cursor.fetchone()
                if (count["count"] if postgres else count[0]) >= 64:
                    raise InvocationStateConflict("native state update limit reached")
                # SQLite's INSERT also checks and locks common ownership before
                # invoking the SDK. This incomplete row can never be committed.
                await cursor.execute(
                    f"INSERT INTO {TABLE} VALUES ({','.join([placeholder] * 5)},NULL)",
                    (
                        request.identity,
                        handle.invocation_id,
                        plan.fingerprint,
                        request.request_digest,
                        request.base_checkpoint,
                    ),
                )
            result = await update(
                saver,
                {
                    **config,
                    "configurable": {
                        **config["configurable"],
                        "checkpoint_id": request.base_checkpoint,
                    },
                },
            )
            selected = result.get("configurable", {})
            if selected.get("thread_id") != thread or selected.get("checkpoint_ns", "") != "":
                raise InvocationStateConflict("native state update changed its scope")
            proof = NativeStateWriteProof(request, selected.get("checkpoint_id"))
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT checkpoint_id,parent_checkpoint_id FROM checkpoints WHERE thread_id="
                    + placeholder
                    + " AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread,),
                )
                row = await cursor.fetchone()
                values = tuple(row.values()) if postgres and row else tuple(row) if row else ()
                if values != (proof.result_checkpoint, request.base_checkpoint):
                    raise InvocationStateConflict("native state update lineage is inconsistent")
                await cursor.execute(
                    f"UPDATE {TABLE} SET result_checkpoint={placeholder} "
                    f"WHERE identity={placeholder}",
                    (proof.result_checkpoint, request.identity),
                )
            if postgres:
                await _guard(conn, fence)
            return proof

    try:
        return await run_native_storage_write(write)
    except OwaError:
        raise
    except Exception:
        raise InvocationStateError("native state update transaction failed") from None
