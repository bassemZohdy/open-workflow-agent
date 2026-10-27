"""Private atomic SDK fork unit; common lineage and resume admission are separate.

The installed coordinator must authorize the operation, reserve the distinct
target and retain its exclusive execution lease. Only SDK writes belong in the
callback. No common persistence, workflow task, tool or model may run there.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.ownership import NativeWriteFence, run_native_storage_write
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.state_lineage import lineage_error
from open_workflow_agent.workflow import WorkflowPlan

from .graph import LangGraphScopeExecutor, _json_identity
from .native_lineage_preflight import NativeLineagePreflight, NativeLineageSnapshot
from .native_state import state_reference
from .native_state_mutations import _checkpoint, _guard, _saver
from .native_state_preflight import _BoundedSaver
from .native_state_storage import guard_checkpoint
from .native_state_transition import projection

TABLE = "owa_state_lineage_v1"
_DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} ("
    "identity TEXT PRIMARY KEY, target_id TEXT NOT NULL UNIQUE, source_id TEXT NOT NULL, "
    "fingerprint TEXT NOT NULL, request_digest TEXT NOT NULL, basis_digest TEXT NOT NULL, "
    "packet_digest TEXT NOT NULL, result_json TEXT)"
)
_PROOF_BYTES = 65_536
_MAX_SCOPES = 64


async def prepare_lineage_storage(saver: Any, *, postgresql: bool) -> None:
    """Deployment setup only, after native saver setup; never on dispatch."""
    if postgresql:
        async with saver.conn.transaction():
            await saver.conn.execute("SELECT pg_advisory_xact_lock(%s,%s)", (0x4F5741, 0x4C463031))
            await saver.conn.execute(_DDL)
            await saver.conn.execute(
                f"CREATE INDEX IF NOT EXISTS owa_lineage_source_v1 ON {TABLE}(source_id)"
            )
    else:
        await saver.conn.execute(_DDL)
        await saver.conn.execute(
            f"CREATE INDEX IF NOT EXISTS owa_lineage_source_v1 ON {TABLE}(source_id)"
        )
        await saver.conn.commit()


@dataclass(frozen=True, slots=True)
class NativeForkPacket:
    reference: str
    occurrence: int
    native_json: str


@dataclass(frozen=True, slots=True)
class NativeForkProposal:
    preflight: NativeLineagePreflight
    packets: tuple[NativeForkPacket, ...]

    @property
    def packet_digest(self) -> str:
        return hashlib.sha256(
            _json_identity(
                [[item.reference, item.occurrence, item.native_json] for item in self.packets]
            ).encode()
        ).hexdigest()

    def verify(
        self, plan: WorkflowPlan, target: ExecutionHandle, bounds: NativeStateConfig
    ) -> None:
        prepared, request = self.preflight, self.preflight.request
        if (
            type(prepared.snapshots) is not tuple
            or type(self.packets) is not tuple
            or any(type(item) is not NativeLineageSnapshot for item in prepared.snapshots)
            or any(type(item) is not NativeForkPacket for item in self.packets)
        ):
            raise lineage_error("fork_packet_invalid")
        prepared.verify_basis(prepared.input_json, prepared.snapshots)
        coordinates = [(item.reference, item.occurrence) for item in prepared.snapshots]
        if (
            target.engine != "langgraph"
            or target.status != "running"
            or target.invocation_id != request.target_invocation_id
            or target.workflow_fingerprint != request.workflow_fingerprint
            or plan.fingerprint != request.workflow_fingerprint
            or not 1 <= len(coordinates) <= min(_MAX_SCOPES, bounds.max_history)
            or len(set(coordinates)) != len(coordinates)
            or ("/do", 1) not in coordinates
            or any(ref not in {scope.reference for scope in plan.scopes} for ref, _ in coordinates)
            or any(type(visit) is not int or visit < 1 for _, visit in coordinates)
            or [(item.reference, item.occurrence) for item in self.packets] != coordinates
            or len(prepared.input_json.encode())
            + sum(
                len(item.native_json.encode()) + len(item.metadata_json.encode())
                for item in prepared.snapshots
            )
            + sum(len(item.native_json.encode()) for item in self.packets)
            > bounds.max_native_bytes * 4
        ):
            raise lineage_error("fork_binding_invalid")
        for item in self.packets:
            value = json.loads(item.native_json)
            if type(value) is not dict or _json_identity(projection(value)) != item.native_json:
                raise lineage_error("fork_packet_invalid")


@dataclass(frozen=True, slots=True)
class NativeForkCheckpoint:
    reference: str
    occurrence: int
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class NativeForkProof:
    identity: str
    request_digest: str
    packet_digest: str
    checkpoints: tuple[NativeForkCheckpoint, ...]


def _config(invocation: str, fingerprint: str, reference: str, occurrence: int) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": LangGraphScopeExecutor.scope_thread_id(
                invocation, fingerprint, reference, occurrence
            ),
            "checkpoint_ns": "",
        }
    }


async def _source_basis(
    saver: Any, postgres: bool, proposal: NativeForkProposal, bounds: NativeStateConfig
) -> None:
    prepared, request = proposal.preflight, proposal.preflight.request
    configs = [
        _config(
            request.source_invocation_id,
            request.workflow_fingerprint,
            item.reference,
            item.occurrence,
        )
        for item in prepared.snapshots
    ]
    bounded = _BoundedSaver(
        saver,
        bounds,
        postgres,
        selected={
            config["configurable"]["thread_id"]: item.checkpoint_id
            for config, item in zip(configs, prepared.snapshots, strict=True)
        },
    )
    for config, expected in zip(configs, prepared.snapshots, strict=True):
        item = await bounded.aget_tuple(config)
        if item is None:
            raise lineage_error("fork_source_missing")
        raw = item.checkpoint["channel_values"]
        if expected.phase == "initial" and set(raw) == {"__start__"}:
            raw = raw["__start__"]
        elif expected.phase != "checkpoint":
            raise lineage_error("fork_source_changed")
        parent = bounded.parents[config["configurable"]["thread_id"]]
        if (
            _json_identity(projection(raw)) != expected.native_json
            or state_reference(
                request.source_invocation_id, request.workflow_fingerprint, expected.checkpoint_id
            )
            != expected.checkpoint_reference
            or (
                state_reference(request.source_invocation_id, request.workflow_fingerprint, parent)
                if parent
                else None
            )
            != expected.parent_reference
            or _json_identity(
                {
                    "created_at": item.checkpoint["ts"],
                    "step": item.metadata["step"],
                    "source": item.metadata["source"],
                    "has_pending_writes": bool(item.pending_writes),
                }
            )
            != expected.metadata_json
        ):
            raise lineage_error("fork_source_changed")


async def _verify_proof(
    saver: Any, postgres: bool, proposal: NativeForkProposal, raw: Any, bounds: NativeStateConfig
) -> NativeForkProof:
    request = proposal.preflight.request
    if type(raw) is not str or len(raw.encode()) > _PROOF_BYTES:
        raise lineage_error("fork_proof_invalid")
    values = json.loads(raw)
    if type(values) is not list or len(values) != len(proposal.packets):
        raise lineage_error("fork_proof_invalid")
    remaining, result = bounds.max_native_bytes, []
    for value, packet, original in zip(
        values, proposal.packets, proposal.preflight.snapshots, strict=True
    ):
        if (
            type(value) is not list
            or len(value) != 3
            or type(value[0]) is not str
            or type(value[1]) is not int
            or value[:2] != [packet.reference, packet.occurrence]
        ):
            raise lineage_error("fork_proof_invalid")
        key = _checkpoint(value[2])
        if key == original.checkpoint_id:
            raise lineage_error("fork_checkpoint_not_new")
        config = _config(
            request.target_invocation_id,
            request.workflow_fingerprint,
            packet.reference,
            packet.occurrence,
        )
        thread = config["configurable"]["thread_id"]
        remaining -= await guard_checkpoint(saver, thread, key, remaining, postgres)
        config["configurable"]["checkpoint_id"] = key
        item = await saver.aget_tuple(config)
        if (
            item is None
            or item.config.get("configurable") != config["configurable"]
            or item.checkpoint.get("id") != key
            or item.checkpoint.get("v") != 4
            or item.parent_config is not None
            or bool(item.pending_writes)
            or item.metadata.get("source") != "update"
            or type(item.metadata.get("step")) is not int
            or _json_identity(projection(item.checkpoint["channel_values"])) != packet.native_json
        ):
            raise lineage_error("fork_proof_changed")
        result.append(NativeForkCheckpoint(packet.reference, packet.occurrence, key))
    return NativeForkProof(
        request.identity.key, request.request_digest, proposal.packet_digest, tuple(result)
    )


async def apply_native_lineage_fork(
    database: str,
    fence: NativeWriteFence,
    target: ExecutionHandle,
    plan: WorkflowPlan,
    proposal: NativeForkProposal,
    write: Callable[[Any, tuple[dict[str, Any], ...]], Awaitable[tuple[dict[str, Any], ...]]],
    *,
    bounds: NativeStateConfig | None = None,
) -> NativeForkProof:
    """Commit all target SDK scopes and proof, or none; never run a workflow.

    Target projections are staged by trusted engine code. This unit proves their
    exact storage, not common receipt remapping, effect safety or resume readiness.
    """
    try:
        limits = NativeStateConfig.model_validate((bounds or NativeStateConfig()).model_dump())
        proposal.verify(plan, target, limits)
        request = proposal.preflight.request
        storage_identity = hashlib.sha256(
            _json_identity(["owa/langgraph-state-lineage/v1", database, plan.fingerprint]).encode()
        ).hexdigest()
        if request.storage_identity != storage_identity:
            raise lineage_error("fork_storage_binding_changed")
        if (
            fence.lease.engine != target.engine
            or fence.lease.invocation_id != target.invocation_id
            or fence.lease.workflow_fingerprint != plan.fingerprint
        ):
            raise lineage_error("fork_ownership_invalid")

        async def transaction() -> NativeForkProof:
            async with (
                asyncio.timeout(limits.timeout_seconds),
                _saver(database, fence, proof_table=TABLE) as (saver, conn, postgres),
            ):
                mark = "%s" if postgres else "?"
                if postgres:
                    # Different targets share the source's finite fork quota.
                    # Serialize that count with its INSERT before target fencing.
                    source_lock = int.from_bytes(
                        hashlib.sha256(request.source_invocation_id.encode()).digest()[:4],
                        signed=True,
                    )
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(%s,%s)", (0x4C463031, source_lock)
                    )
                    await _guard(conn, fence)
                async with conn.cursor() as cursor:
                    if not postgres:
                        await cursor.execute(
                            f"UPDATE {TABLE} SET request_digest=request_digest WHERE identity=?",
                            (request.identity.key,),
                        )
                    size = "octet_length" if postgres else "length"
                    fields = [
                        ("target_id", 256),
                        ("source_id", 256),
                        ("fingerprint", 256),
                        ("request_digest", 64),
                        ("basis_digest", 64),
                        ("packet_digest", 64),
                        ("result_json", _PROOF_BYTES),
                    ]
                    await cursor.execute(
                        "SELECT "
                        + ",".join(
                            f"CASE WHEN {size}({name})<={bound} THEN {name} ELSE NULL END AS {name}"
                            for name, bound in fields
                        )
                        + f" FROM {TABLE} WHERE identity={mark}",
                        (request.identity.key,),
                    )
                    row = await cursor.fetchone()
                    binding = (
                        request.target_invocation_id,
                        request.source_invocation_id,
                        plan.fingerprint,
                        request.request_digest,
                        request.source_basis_digest,
                        proposal.packet_digest,
                    )
                    if row is not None:
                        retained = tuple(row.values()) if postgres else tuple(row)
                        if retained[:6] != binding:
                            raise InvocationStateConflict("native fork request changed")
                        return await _verify_proof(saver, postgres, proposal, retained[6], limits)
                    await cursor.execute(
                        f"SELECT COUNT(*) AS count FROM {TABLE} WHERE source_id={mark}",
                        (request.source_invocation_id,),
                    )
                    count = await cursor.fetchone()
                    if (count["count"] if postgres else count[0]) >= 64:
                        raise lineage_error("fork_limit")
                    configs = tuple(
                        _config(
                            request.target_invocation_id,
                            plan.fingerprint,
                            item.reference,
                            item.occurrence,
                        )
                        for item in proposal.packets
                    )
                    for config in configs:
                        tables = (
                            ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
                            if postgres
                            else ("checkpoints", "writes")
                        )
                        for table in tables:
                            await cursor.execute(
                                f"SELECT 1 FROM {table} WHERE thread_id={mark} LIMIT 1",
                                (config["configurable"]["thread_id"],),
                            )
                            if await cursor.fetchone() is not None:
                                raise InvocationStateConflict(
                                    "native fork target already has native state"
                                )
                    await _source_basis(saver, postgres, proposal, limits)
                    await cursor.execute(
                        f"INSERT INTO {TABLE} VALUES ({','.join([mark] * 7)},NULL)",
                        (request.identity.key, *binding),
                    )
                result = await write(
                    saver, tuple(json.loads(_json_identity(item)) for item in configs)
                )
                if type(result) is not tuple or len(result) != len(configs):
                    raise lineage_error("fork_result_invalid")
                records = []
                for item, config, packet in zip(result, configs, proposal.packets, strict=True):
                    selected = item.get("configurable", {})
                    if set(selected) != {"thread_id", "checkpoint_ns", "checkpoint_id"} or any(
                        selected.get(key) != value for key, value in config["configurable"].items()
                    ):
                        raise lineage_error("fork_result_scope_changed")
                    records.append([packet.reference, packet.occurrence, selected["checkpoint_id"]])
                raw = _json_identity(records)
                proof = await _verify_proof(saver, postgres, proposal, raw, limits)
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        f"UPDATE {TABLE} SET result_json={mark} WHERE identity={mark}",
                        (raw, request.identity.key),
                    )
                if postgres:
                    await _guard(conn, fence)
                return proof

        return await run_native_storage_write(transaction)
    except OwaError:
        raise
    except Exception:
        raise lineage_error("fork_transaction_failed") from None
