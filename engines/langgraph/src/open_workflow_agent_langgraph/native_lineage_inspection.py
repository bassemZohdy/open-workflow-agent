"""Bounded native fork evidence for the common lineage verifier.

Only a digest crosses the engine boundary. Native checkpoint coordinates and
projected state remain private and are read from the pinned SDK's saver.
"""

from __future__ import annotations

import hashlib
import json
from importlib import import_module
from typing import TYPE_CHECKING, Any

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.effects import EffectJournal, ReceiptDependency
from open_workflow_agent.lineage_registry import LineageRegistry, lineage_identity
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.state_lineage import StateLineageRequest, lineage_error

from .graph import LangGraphScopeExecutor, _json_identity
from .native_lineage_fork import TABLE, verify_fork_schedule
from .native_lineage_preflight import NativeLineagePreflight
from .native_state_controls import _proof_connection
from .native_state_mutations import _checkpoint
from .native_state_storage import guard_checkpoint_sync
from .native_state_transition import projection

if TYPE_CHECKING:
    from open_workflow_agent.services import RuntimeServices


def lineage_registry_for_target(
    services: RuntimeServices,
    handle: ExecutionHandle,
    bounds: NativeStateConfig,
    *,
    available_bytes: int | None = None,
) -> LineageRegistry | None:
    """Reconstruct verified lineage without acquiring ownership or writing state."""
    journal = services.effect_journal
    identity = lineage_identity(handle)
    indexed = journal.connection.execute(
        "SELECT 1 FROM workflow_effect_invocations WHERE identity=?", (identity.key,)
    ).fetchone()
    if indexed is None and journal.get(identity) is None:
        return None
    proof_bounds = bounds
    if available_bytes is not None:
        if available_bytes < 1024:
            raise lineage_error("native_proof_budget")
        proof_bounds = NativeStateConfig.model_validate(
            {
                **bounds.model_dump(),
                "max_native_bytes": min(available_bytes, bounds.max_native_bytes),
            }
        )
    database = services.engine_database_path("langgraph", create_namespace=False)
    registry = LineageRegistry(
        journal,
        services.invocations,
        services.execution_authorizations,
        lambda request, digest: inspect_native_lineage(database, request, proof_bounds) == digest,
    )
    registry.read(handle)
    return registry


def selected_business_receipts(
    preflight: NativeLineagePreflight, journal: EffectJournal
) -> tuple[ReceiptDependency, ...]:
    """Extract only verified business dependencies present in the selected basis.

    Callers must use an authorized preflight obtained from native inspection;
    a self-constructed digest is not evidence of source checkpoint membership.
    """
    try:
        preflight.verify_basis(preflight.input_json, preflight.snapshots)
        selected: dict[str, ReceiptDependency] = {}
        for snapshot in preflight.snapshots:
            native = json.loads(snapshot.native_json)
            bindings = json.loads(native["receipt_bindings"])
            receipts = bindings["receipts"]
            if type(receipts) is not list:
                raise lineage_error("source_receipts_invalid")
            for value in receipts:
                dependency = ReceiptDependency.from_value(value)
                identity = dependency.identity
                if (
                    identity.invocation_id != preflight.request.source_invocation_id
                    or identity.workflow_fingerprint != preflight.request.workflow_fingerprint
                ):
                    raise lineage_error("source_receipts_invalid")
                if not (
                    identity.operation.startswith(("call:", "run:"))
                    or identity.operation in {"emit", "listen"}
                ):
                    continue
                dependency.verify(journal)
                previous = selected.setdefault(identity.key, dependency)
                if previous != dependency or len(selected) > 64:
                    raise lineage_error("source_receipts_invalid")
        return tuple(selected[key] for key in sorted(selected))
    except Exception:
        raise lineage_error("source_receipts_unavailable") from None


def inspect_native_lineage(
    database: str, request: StateLineageRequest, bounds: NativeStateConfig
) -> str | None:
    """Re-read the stored fork and return a digest usable by common lineage.

    Target checkpoint history may advance after the fork. Its retained original
    checkpoints are inspected by exact ID; later task writes are not replayed
    during inspection. No native ID or packet is returned to the caller.
    """
    binding = hashlib.sha256(
        _json_identity(
            ["owa/langgraph-state-lineage/v1", database, request.workflow_fingerprint]
        ).encode()
    ).hexdigest()
    if binding != request.storage_identity:
        raise lineage_error("fork_storage_binding_changed")
    try:
        limits = NativeStateConfig.model_validate(bounds.model_dump())
        with _proof_connection(database) as (connection, postgres):
            mark = "%s" if postgres else "?"
            size = "octet_length" if postgres else "length(CAST({name} AS BLOB))"
            fields = [
                ("target_id", 256),
                ("source_id", 256),
                ("fingerprint", 256),
                ("request_digest", 64),
                ("basis_digest", 64),
                ("packet_digest", 64),
                ("result_json", 65_536),
            ]
            expressions = []
            for name, maximum in fields:
                length = f"{size}({name})" if postgres else size.format(name=name)
                expressions.append(f"CASE WHEN {length}<={maximum} THEN {name} ELSE NULL END")
            row = connection.execute(
                "SELECT " + ",".join(expressions) + f" FROM {TABLE} WHERE identity={mark}",
                (request.identity.key,),
            ).fetchone()
            if row is None:
                return None
            if tuple(row[:5]) != (
                request.target_invocation_id,
                request.source_invocation_id,
                request.workflow_fingerprint,
                request.request_digest,
                request.source_basis_digest,
            ):
                raise lineage_error("fork_proof_changed")
            packet_digest, raw = row[5], row[6]
            if type(packet_digest) is not str or len(packet_digest) != 64 or type(raw) is not str:
                raise lineage_error("fork_proof_changed")
            records = json.loads(raw)
            if type(records) is not list or not 1 <= len(records) <= min(64, limits.max_history):
                raise lineage_error("fork_proof_changed")
            sdk = import_module(
                "langgraph.checkpoint.postgres" if postgres else "langgraph.checkpoint.sqlite"
            )
            saver = sdk.PostgresSaver(connection) if postgres else sdk.SqliteSaver(connection)
            if not postgres:
                saver.is_setup = True
            remaining, packets, seen = limits.max_native_bytes, [], set()
            for entry in records:
                if (
                    type(entry) is not list
                    or len(entry) != 3
                    or type(entry[0]) is not str
                    or type(entry[1]) is not int
                    or entry[1] < 1
                    or not 0 < len(entry[0].encode()) <= 4096
                ):
                    raise lineage_error("fork_proof_changed")
                reference, occurrence, key = entry[0], entry[1], _checkpoint(entry[2])
                if (reference, occurrence) in seen:
                    raise lineage_error("fork_proof_changed")
                seen.add((reference, occurrence))
                thread = LangGraphScopeExecutor.scope_thread_id(
                    request.target_invocation_id,
                    request.workflow_fingerprint,
                    reference,
                    occurrence,
                )
                remaining -= guard_checkpoint_sync(connection, thread, key, remaining, postgres)
                config: dict[str, Any] = {
                    "configurable": {"thread_id": thread, "checkpoint_ns": "", "checkpoint_id": key}
                }
                item = saver.get_tuple(config)
                if (
                    item is None
                    or item.config.get("configurable") != config["configurable"]
                    or item.checkpoint.get("id") != key
                    or item.checkpoint.get("v") != 4
                    or item.parent_config is not None
                    or item.metadata.get("source") != "update"
                    or type(item.metadata.get("step")) is not int
                ):
                    raise lineage_error("fork_proof_changed")
                verify_fork_schedule(item.checkpoint)
                packets.append(
                    [
                        reference,
                        occurrence,
                        _json_identity(projection(item.checkpoint["channel_values"])),
                    ]
                )
            if ("/do", 1) not in seen or hashlib.sha256(
                _json_identity(packets).encode()
            ).hexdigest() != packet_digest:
                raise lineage_error("fork_proof_changed")
            return hashlib.sha256(
                _json_identity(
                    [
                        "owa/native-lineage-proof/v1",
                        request.request_digest,
                        request.source_basis_digest,
                        packet_digest,
                        records,
                    ]
                ).encode()
            ).hexdigest()
    except Exception:
        raise lineage_error("native_proof_unavailable") from None
