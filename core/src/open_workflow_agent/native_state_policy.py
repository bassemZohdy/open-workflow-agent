"""Immutable declared state-update policy; this does not enable native mutation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

from .errors import ModelError, UnsupportedWorkflowFeature, WorkflowSemanticError
from .model_responses import bounded_model_json
from .native_extensions import LANGGRAPH_STATE_UPDATE, NativeExtensionRequirement
from .resources import schema_references

CAPABILITY = LANGGRAPH_STATE_UPDATE
_ROOT = re.compile(r"/do/(0|[1-9][0-9]*)")


def _invalid(message: str) -> WorkflowSemanticError:
    return WorkflowSemanticError(message, details={"extension": CAPABILITY})


def _encode(value: Any) -> str:
    try:
        return bounded_model_json(value, limit=65_536)
    except ModelError:
        raise _invalid(
            "native state policy and update values must be finite bounded JSON"
        ) from None


@dataclass(frozen=True, slots=True)
class NativeStateUpdatePolicy:
    allowed_task_refs: tuple[str, ...]
    schema_json: str
    authorization_policy_ref: str

    @property
    def schema(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.schema_json)
        return value

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "profile": CAPABILITY,
            "allowedTaskRefs": list(self.allowed_task_refs),
            "updateSchema": self.schema,
            "authorizationPolicyRef": self.authorization_policy_ref,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_encode(self.descriptor).encode()).hexdigest()

    def validate(self, task_reference: str, values: Any, authorization_policy_ref: str) -> str:
        """Return detached canonical values only after exact policy/schema checks."""
        if task_reference not in self.allowed_task_refs:
            raise _invalid("state update attribution is outside the declared task references")
        if authorization_policy_ref != self.authorization_policy_ref:
            raise _invalid("state update authorization policy binding changed")
        if type(values) is not dict:
            raise _invalid("state update values must be an object")
        encoded = _encode(values)
        try:
            error = next(
                Draft202012Validator(self.schema, registry=Registry()).iter_errors(
                    json.loads(encoded)
                ),
                None,
            )
            if error is not None:
                raise _invalid("state update values do not match the declared schema")
        except WorkflowSemanticError:
            raise
        except Exception:
            raise _invalid("state update schema could not validate the supplied values") from None
        return encoded


def native_state_update_policy(
    workflow: Mapping[str, Any], requirements: tuple[NativeExtensionRequirement, ...]
) -> NativeStateUpdatePolicy | None:
    document = workflow.get("document")
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    owa = metadata.get("owa") if isinstance(metadata, Mapping) else None
    payloads = owa.get("nativeExtensionData", {}) if isinstance(owa, Mapping) else {}
    if not isinstance(payloads, Mapping):
        raise _invalid("native extension data must be an object")
    declared = any(item.required and item.capability_id == CAPABILITY for item in requirements)
    if CAPABILITY not in payloads:
        if declared:
            raise _invalid("native state updates require an explicit declared policy")
        return None
    if not declared:
        raise _invalid("native state policy requires its exact required extension declaration")
    payload = payloads[CAPABILITY]
    if not isinstance(payload, Mapping) or set(payload) != {
        "allowedTaskRefs",
        "updateSchema",
        "authorizationPolicyRef",
    }:
        raise _invalid("native state policy fields are invalid")
    _encode(dict(payload))
    refs, schema, policy = (
        payload[key]
        for key in (
            "allowedTaskRefs",
            "updateSchema",
            "authorizationPolicyRef",
        )
    )
    if not isinstance(refs, list) or not 1 <= len(refs) <= 64:
        raise _invalid("native state policy requires between one and 64 task references")
    if (
        type(policy) is not str
        or not 0 < len(policy.encode()) <= 128
        or any(ord(character) < 32 for character in policy)
    ):
        raise _invalid("native state authorization policy reference is invalid")
    tasks = workflow.get("do")
    if not isinstance(tasks, list):
        raise _invalid("native state policy requires a root task sequence")
    selected: list[str] = []
    for ref in refs:
        if type(ref) is not str or len(ref) > 32 or not _ROOT.fullmatch(ref):
            raise _invalid("native state policy attribution must use root /do/<index> references")
        index = int(ref.rsplit("/", 1)[1])
        if index >= len(tasks) or not isinstance(tasks[index], Mapping) or len(tasks[index]) != 1:
            raise _invalid("native state policy references an unknown root task")
        name, task = next(iter(tasks[index].items()))
        if not isinstance(task, Mapping) or "set" not in task:
            raise UnsupportedWorkflowFeature(
                "state update attribution requires an explicit root set task"
            )
        canonical = ref + "/" + str(name).replace("~", "~0").replace("/", "~1")
        if len(canonical.encode()) > 4096 or any(ord(character) < 32 for character in canonical):
            raise _invalid("native state task attribution exceeds its bound")
        if canonical in selected:
            raise _invalid("native state policy contains duplicate task references")
        selected.append(canonical)
    if type(schema) is not dict:
        raise _invalid("native state updateSchema must be a JSON Schema object")
    schema_json = _encode(schema)
    try:
        missing = schema_references(
            schema, Registry(), "/document/metadata/owa/nativeExtensionData/" + CAPABILITY
        )
    except (WorkflowSemanticError, UnsupportedWorkflowFeature):
        raise
    except Exception:
        raise _invalid("native state update schema is invalid") from None
    if missing:
        raise UnsupportedWorkflowFeature(
            "native state update schemas require an offline reference closure"
        )
    selected.sort(key=lambda value: int(value.split("/")[2]))
    result = NativeStateUpdatePolicy(tuple(selected), schema_json, policy)
    _ = result.digest
    return result
