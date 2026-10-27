"""Declared checkpoint replay/branch policy; execution remains separately gated."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .errors import WorkflowSemanticError
from .native_extensions import (
    LANGGRAPH_STATE_BRANCH,
    LANGGRAPH_STATE_REPLAY,
    NativeExtensionRequirement,
)
from .native_state_policy import NativeStateUpdatePolicy

LineageKind = Literal["replay", "branch"]
EffectPolicy = Literal["read_only", "reexecute"]


def lineage_policy_error() -> WorkflowSemanticError:
    return WorkflowSemanticError("native state lineage requires an exact declared policy")


@dataclass(frozen=True, slots=True)
class NativeLineagePolicy:
    kind: LineageKind
    authorization_policy_ref: str
    effect_policy: EffectPolicy

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not str
            or self.kind not in {"replay", "branch"}
            or type(self.effect_policy) is not str
            or self.effect_policy not in {"read_only", "reexecute"}
            or type(self.authorization_policy_ref) is not str
            or not 0 < len(self.authorization_policy_ref.encode()) <= 128
            or any(ord(char) < 32 for char in self.authorization_policy_ref)
        ):
            raise lineage_policy_error()

    @property
    def capability(self) -> str:
        return LANGGRAPH_STATE_REPLAY if self.kind == "replay" else LANGGRAPH_STATE_BRANCH

    @property
    def digest(self) -> str:
        value = [self.capability, self.authorization_policy_ref, self.effect_policy]
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()


def native_lineage_policies(
    workflow: Mapping[str, Any],
    requirements: tuple[NativeExtensionRequirement, ...],
    updates: NativeStateUpdatePolicy | None,
) -> tuple[NativeLineagePolicy | None, NativeLineagePolicy | None]:
    document = workflow.get("document")
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    owa = metadata.get("owa") if isinstance(metadata, Mapping) else None
    payloads = owa.get("nativeExtensionData", {}) if isinstance(owa, Mapping) else {}
    if not isinstance(payloads, Mapping):
        raise lineage_policy_error()
    result: list[NativeLineagePolicy | None] = []
    for kind, capability, field in (
        ("replay", LANGGRAPH_STATE_REPLAY, "replayEffectPolicy"),
        ("branch", LANGGRAPH_STATE_BRANCH, "branchEffectPolicy"),
    ):
        declared = any(item.required and item.capability_id == capability for item in requirements)
        if capability not in payloads:
            if declared:
                raise lineage_policy_error()
            result.append(None)
            continue
        value = payloads[capability]
        if (
            not declared
            or not isinstance(value, Mapping)
            or set(value) != {"authorizationPolicyRef", field}
        ):
            raise lineage_policy_error()
        policy = NativeLineagePolicy(kind, value["authorizationPolicyRef"], value[field])  # type: ignore[arg-type]
        if kind == "branch" and (
            updates is None or updates.authorization_policy_ref != policy.authorization_policy_ref
        ):
            raise WorkflowSemanticError(
                "native state branching requires the matching declared update policy"
            )
        result.append(policy)
    return result[0], result[1]
