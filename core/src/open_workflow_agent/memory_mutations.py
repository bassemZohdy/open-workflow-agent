"""Finite, framework-neutral requests for the private native memory write profile."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from .effects import EffectIdentity, effect_request_digest
from .errors import InvocationStateError, ModelError
from .memory_reads import MemoryReadBinding
from .memory_retention import MemoryRecordRetention
from .model_responses import bounded_model_json
from .persistence import ExecutionHandle, InvocationStore, _verify_identity

PROFILE = "owa/memory-mutation/v1"
MAX_MUTATIONS = 64
TABLE = "owa_memory_mutations_v1"


def mutation_error(reason: str) -> InvocationStateError:
    return InvocationStateError(
        "native memory mutation evidence is unavailable or inconsistent",
        details={"stage": "memory", "reason": reason},
    )


@dataclass(frozen=True, slots=True)
class MemoryMutation:
    """One logical occurrence/key; changing its binding or value is a conflict."""

    parent: EffectIdentity
    binding: MemoryReadBinding
    namespace: str
    collection: str
    key: str
    kind: Literal["put", "delete"]
    value_json: str | None
    max_record_bytes: int = 65_536
    retention: MemoryRecordRetention | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.parent, EffectIdentity)
            or not isinstance(self.binding, MemoryReadBinding)
            or self.binding.backend not in {"sqlite", "postgresql"}
            or type(self.namespace) is not str
            or not re.fullmatch(r"owa/principal-memory/v1:[a-f0-9]{64}", self.namespace)
            or type(self.collection) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.collection)
            or type(self.key) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", self.key)
            or type(self.kind) is not str
            or self.kind not in {"put", "delete"}
            or type(self.max_record_bytes) is not int
            or not 1 <= self.max_record_bytes <= 65_536
            or (
                self.retention is not None and not isinstance(self.retention, MemoryRecordRetention)
            )
        ):
            raise mutation_error("request_invalid")
        if self.kind == "delete":
            if self.value_json is not None:
                raise mutation_error("value_invalid")
        else:
            if (
                type(self.value_json) is not str
                or len(self.value_json.encode()) > self.max_record_bytes
            ):
                raise mutation_error("value_invalid")
            try:
                value = json.loads(self.value_json)
                if (
                    type(value) is not dict
                    or bounded_model_json(value, limit=self.max_record_bytes) != self.value_json
                ):
                    raise mutation_error("value_invalid")
            except (ValueError, ModelError, RecursionError):
                raise mutation_error("value_invalid") from None
        # Bound parent identity/occurrence and the complete request before SQL.
        try:
            bounded_model_json(self.descriptor, limit=131_072)
        except ModelError:
            raise mutation_error("request_invalid") from None

    @classmethod
    def put(
        cls,
        parent: EffectIdentity,
        binding: MemoryReadBinding,
        namespace: str,
        collection: str,
        key: str,
        value: dict[str, Any],
        *,
        max_record_bytes: int = 65_536,
        retention: MemoryRecordRetention | None = None,
    ) -> MemoryMutation:
        if (
            type(value) is not dict
            or type(max_record_bytes) is not int
            or not 1 <= max_record_bytes <= 65_536
        ):
            raise mutation_error("value_invalid")
        try:
            encoded = bounded_model_json(value, limit=max_record_bytes)
        except ModelError:
            raise mutation_error("value_invalid") from None
        return cls(
            parent, binding, namespace, collection, key, "put", encoded, max_record_bytes, retention
        )

    @property
    def identity(self) -> EffectIdentity:
        slot = effect_request_digest(
            PROFILE + ":slot",
            {
                "parent_operation": self.parent.operation,
                "kind": self.kind,
                "collection": self.collection,
                "key": self.key,
            },
        )
        return replace(self.parent, operation="policy:memory-mutation:v1:" + slot)

    @property
    def descriptor(self) -> dict[str, Any]:
        value = {
            "profile": PROFILE,
            "parent": {
                **asdict(self.parent),
                "occurrence": [list(item) for item in self.parent.occurrence],
            },
            "binding": asdict(self.binding),
            "namespace": self.namespace,
            "collection": self.collection,
            "key": self.key,
            "kind": self.kind,
            "value": None if self.value_json is None else json.loads(self.value_json),
            "max_record_bytes": self.max_record_bytes,
        }
        if self.retention is not None:
            value["retention"] = self.retention.descriptor
        return value

    @property
    def request_digest(self) -> str:
        return effect_request_digest(PROFILE + ":request", self.descriptor)

    @classmethod
    def from_descriptor(cls, raw: Any) -> MemoryMutation:
        if (
            type(raw) is not dict
            or (set(raw) - {"retention"})
            != {
                "profile",
                "parent",
                "binding",
                "namespace",
                "collection",
                "key",
                "kind",
                "value",
                "max_record_bytes",
            }
            or raw["profile"] != PROFILE
        ):
            raise mutation_error("request_invalid")
        binding = raw["binding"]
        if type(binding) is not dict or set(binding) != {"id", "version", "backend"}:
            raise mutation_error("binding_invalid")
        parent = EffectIdentity.from_value(raw["parent"])
        retention = (
            MemoryRecordRetention.from_descriptor(raw["retention"]) if "retention" in raw else None
        )
        if raw["kind"] == "put":
            return cls.put(
                parent,
                MemoryReadBinding(**binding),
                raw["namespace"],
                raw["collection"],
                raw["key"],
                raw["value"],
                max_record_bytes=raw["max_record_bytes"],
                retention=retention,
            )
        if raw["kind"] != "delete" or raw["value"] is not None:
            raise mutation_error("value_invalid")
        return cls(
            parent,
            MemoryReadBinding(**binding),
            raw["namespace"],
            raw["collection"],
            raw["key"],
            "delete",
            None,
            raw["max_record_bytes"],
            retention,
        )

    def verify_invocation(self, invocations: InvocationStore, handle: ExecutionHandle) -> None:
        saved = invocations.get(handle.invocation_id)
        if (
            saved is None
            or saved._persisted_payload is None
            or saved.status != "running"
            or handle.status != "running"
            or handle._execution_lease is None
            or handle.owner_principal is None
            or self.parent.invocation_id != handle.invocation_id
            or self.parent.workflow_fingerprint != handle.workflow_fingerprint
            or self.namespace
            != "owa/principal-memory/v1:"
            + hashlib.sha256(handle.owner_principal.encode()).hexdigest()
        ):
            raise mutation_error("invocation_invalid")
        _verify_identity(handle, json.loads(saved._persisted_payload))


@dataclass(frozen=True, slots=True)
class MemoryMutationMarker:
    """Native transaction evidence, not a common effect completion receipt."""

    identity: str
    invocation_id: str
    fingerprint: str
    request_digest: str

    @classmethod
    def for_request(cls, request: MemoryMutation) -> MemoryMutationMarker:
        return cls(
            request.identity.key,
            request.parent.invocation_id,
            request.parent.workflow_fingerprint,
            request.request_digest,
        )
