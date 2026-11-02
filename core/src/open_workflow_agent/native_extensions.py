"""Versioned workflow metadata declarations for engine-native capabilities.

This module only defines the declaration and admission boundary. Feature
implementations remain owned by an engine and must be advertised there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ExpressionError, UnsupportedWorkflowFeature, WorkflowSemanticError
from .expressions import validate_expression_data

_EXTENSION_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")
_EXTENSION_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_MAX_DECLARATIONS = 32
_MAX_NAME_LENGTH = 128
_MAX_VERSION_LENGTH = 64
_MAX_CHANNELS = 64
_MAX_CHANNEL_READS = 256
_MAX_CHANNEL_WRITES = 4096
_MAX_CHANNEL_INITIAL_BYTES = 65536
_MAX_CHANNEL_INITIAL_TOTAL_BYTES = 262144
MAX_CHANNEL_VALUE_BYTES = 65536
MAX_CHANNEL_VALUE_TOTAL_BYTES = 262144
LANGGRAPH_CHANNELS = "langgraph.channels@1.0.0"
LANGGRAPH_COMMAND = "langgraph.command@1.0.0"
LANGGRAPH_SEND = "langgraph.send@1.0.0"
LANGGRAPH_STREAMS = "langgraph.streams@1.0.0"
LANGGRAPH_STREAM_TASKS = "langgraph.streams.tasks@1.0.0"
LANGGRAPH_STREAM_CHECKPOINTS = "langgraph.streams.checkpoints@1.0.0"
LANGGRAPH_STREAM_DEBUG = "langgraph.streams.debug@1.0.0"
LANGGRAPH_STREAM_CUSTOM = "langgraph.streams.custom@1.0.0"
LANGGRAPH_STREAM_SUBGRAPHS = "langgraph.streams.subgraphs@1.0.0"
LANGGRAPH_STATE_UPDATE = "langgraph.state.update@1.0.0"
LANGGRAPH_STATE_REPLAY = "langgraph.state.replay@1.0.0"
LANGGRAPH_STATE_BRANCH = "langgraph.state.branch@1.0.0"
MAX_NATIVE_SEND_BRANCHES = 64
_CHANNEL_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TASK_REFERENCE = re.compile(r"^/do/(0|[1-9][0-9]*)$")
NATIVE_CHANNEL_VARIABLE = "owa_native_channels"


@dataclass(frozen=True, slots=True)
class NativeExtensionRequirement:
    name: str
    version: str
    required: bool = True

    @property
    def capability_id(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class NativeChannel:
    channel_id: str
    reducer: str
    initial_json: str
    writers: tuple[str, ...]

    @property
    def initial(self) -> Any:
        return json.loads(self.initial_json)


@dataclass(frozen=True, slots=True)
class NativeChannelRead:
    task: str
    channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeChannelConfig:
    channels: tuple[NativeChannel, ...]
    reads: tuple[NativeChannelRead, ...]


@dataclass(frozen=True, slots=True)
class NativeCustomStreamEvent:
    task: str
    name: str
    data_json: str
    from_output: bool

    @property
    def data(self) -> Any:
        return json.loads(self.data_json)


@dataclass(frozen=True, slots=True)
class NativeCustomStreamConfig:
    events: tuple[NativeCustomStreamEvent, ...]


def native_custom_stream_config(
    workflow: Mapping[str, Any], requirements: tuple[NativeExtensionRequirement, ...]
) -> NativeCustomStreamConfig | None:
    document = workflow.get("document")
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    owa = metadata.get("owa") if isinstance(metadata, Mapping) else None
    if not isinstance(owa, Mapping):
        return None
    payloads = owa.get("nativeExtensionData", {})
    required = {item.capability_id for item in requirements if item.required}
    if not isinstance(payloads, Mapping) or LANGGRAPH_STREAM_CUSTOM not in payloads:
        if LANGGRAPH_STREAM_CUSTOM in required:
            raise WorkflowSemanticError("required native custom stream needs event bindings")
        return None
    if not {LANGGRAPH_STREAM_CUSTOM, LANGGRAPH_STREAMS} <= required:
        raise WorkflowSemanticError(
            "native custom stream data requires both required stream profiles"
        )
    payload = payloads[LANGGRAPH_STREAM_CUSTOM]
    if not isinstance(payload, Mapping) or set(payload) != {"events"}:
        raise WorkflowSemanticError("native custom stream data must contain only events")
    entries = payload["events"]
    do = workflow.get("do")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 64 or not isinstance(do, list):
        raise WorkflowSemanticError(
            "native custom stream events must contain 1 to 64 root bindings"
        )
    events: list[NativeCustomStreamEvent] = []
    seen: set[tuple[str, str]] = set()
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"task", "name", "data"}:
            raise WorkflowSemanticError("native custom event must contain only task, name and data")
        task, name, source = entry["task"], entry["name"], entry["data"]
        if not isinstance(task, str) or len(task) > 64 or not _TASK_REFERENCE.fullmatch(task):
            raise WorkflowSemanticError("native custom event task must be a direct root reference")
        index = int(task.rsplit("/", 1)[1])
        if index >= len(do) or not isinstance(do[index], Mapping) or len(do[index]) != 1:
            raise WorkflowSemanticError("native custom event references an unknown root task")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name):
            raise WorkflowSemanticError("native custom event name is invalid")
        pair = (task, name)
        if pair in seen:
            raise WorkflowSemanticError("duplicate native custom event binding")
        seen.add(pair)
        if not isinstance(source, Mapping) or (
            set(source) != {"literal"} and source != {"from": "task_output"}
        ):
            raise WorkflowSemanticError(
                "native custom event data must select literal or task_output"
            )
        from_output = source == {"from": "task_output"}
        data = None if from_output else source["literal"]
        try:
            validate_expression_data(data)
            encoded = json.dumps(
                data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
            )
        except (ExpressionError, TypeError, ValueError) as exc:
            raise WorkflowSemanticError("native custom event data is invalid") from exc
        size = len(encoded.encode("utf-8"))
        total_bytes += size
        if size > 65_536 or total_bytes > 262_144:
            raise WorkflowSemanticError("native custom event templates exceed their size bounds")
        task_name = str(next(iter(do[index]))).replace("~", "~0").replace("/", "~1")
        events.append(NativeCustomStreamEvent(f"{task}/{task_name}", name, encoded, from_output))
    return NativeCustomStreamConfig(tuple(events))


def native_channel_config(
    workflow: Mapping[str, Any], requirements: tuple[NativeExtensionRequirement, ...]
) -> NativeChannelConfig | None:
    """Validate the finite LangGraph channels extension payload, when present."""
    document = workflow.get("document")
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    owa = metadata.get("owa") if isinstance(metadata, Mapping) else None
    if not isinstance(owa, Mapping):
        return None

    payloads = owa.get("nativeExtensionData", {})
    if not isinstance(payloads, Mapping):
        raise WorkflowSemanticError("document.metadata.owa.nativeExtensionData must be an object")
    if not payloads:
        return None
    if set(payloads) - {
        LANGGRAPH_CHANNELS,
        LANGGRAPH_STATE_UPDATE,
        LANGGRAPH_STATE_REPLAY,
        LANGGRAPH_STATE_BRANCH,
        LANGGRAPH_STREAM_CUSTOM,
    }:
        raise WorkflowSemanticError("native extension data is not supported")
    if LANGGRAPH_CHANNELS not in payloads:
        return None
    declarations = {item.capability_id: item for item in requirements}
    declaration = declarations.get(LANGGRAPH_CHANNELS)
    if declaration is None or not declaration.required:
        raise WorkflowSemanticError(
            "LangGraph channel data requires its exact required native extension declaration"
        )

    payload = payloads[LANGGRAPH_CHANNELS]
    if not isinstance(payload, Mapping) or set(payload) != {"channels", "reads"}:
        raise WorkflowSemanticError("LangGraph channel data must contain only channels and reads")
    channel_items, read_items = payload["channels"], payload["reads"]
    if not isinstance(channel_items, list) or not isinstance(read_items, list):
        raise WorkflowSemanticError("LangGraph channels and reads must be arrays")

    do = workflow.get("do")
    if not isinstance(do, list):
        raise WorkflowSemanticError("LangGraph channel bindings require a root task sequence")
    root_references = [f"/do/{index}" for index in range(len(do))]
    root_reference_set = set(root_references)

    def task_reference(value: Any, label: str) -> str:
        if not isinstance(value, str) or not _TASK_REFERENCE.fullmatch(value):
            raise WorkflowSemanticError(f"{label} must be a direct root /do/<index> reference")
        if value not in root_reference_set:
            raise WorkflowSemanticError(f"{label} references an unknown root task")
        index = int(value.rsplit("/", 1)[1])
        task_item = do[index]
        if not isinstance(task_item, Mapping) or len(task_item) != 1:
            raise WorkflowSemanticError(f"{label} must reference a named root task")
        task_name = str(next(iter(task_item)))
        task_name = task_name.replace("~", "~0").replace("/", "~1")
        # Extension declarations address the stable root sequence index;
        # runtime bindings use the canonical task reference including its name.
        return f"{value}/{task_name}"

    if len(channel_items) > _MAX_CHANNELS:
        raise WorkflowSemanticError("native channel count exceeds the limit of 64")
    if len(read_items) > _MAX_CHANNEL_READS:
        raise WorkflowSemanticError("native channel read count exceeds the limit of 256")

    channels: list[NativeChannel] = []
    channel_ids: set[str] = set()
    writer_pairs: set[tuple[str, str]] = set()
    writer_count = 0
    initial_bytes = 0
    for index, item in enumerate(channel_items):
        reference = (
            f"/document/metadata/owa/nativeExtensionData/{LANGGRAPH_CHANNELS}/channels/{index}"
        )
        if not isinstance(item, Mapping) or set(item) != {
            "id",
            "reducer",
            "initial",
            "writes",
        }:
            raise WorkflowSemanticError(
                "native channel must contain only id, reducer, initial and writes",
                details={"reference": reference},
            )
        channel_id, reducer, writes = item["id"], item["reducer"], item["writes"]
        if not isinstance(channel_id, str) or not _CHANNEL_ID.fullmatch(channel_id):
            raise WorkflowSemanticError(
                "native channel id must be a lowercase identifier", details={"reference": reference}
            )
        if channel_id in channel_ids:
            raise WorkflowSemanticError("duplicate native channel id", details={"id": channel_id})
        channel_ids.add(channel_id)
        if not isinstance(reducer, str) or reducer not in {"append", "last_value"}:
            raise WorkflowSemanticError(
                "native channel reducer must be append or last_value",
                details={"reference": f"{reference}/reducer"},
            )
        if not isinstance(writes, list):
            raise WorkflowSemanticError("native channel writes must be an array")
        writers: list[str] = []
        for write in writes:
            if not isinstance(write, Mapping) or set(write) != {"task"}:
                raise WorkflowSemanticError("native channel write must contain only task")
            task = task_reference(write["task"], "native channel writer")
            pair = (task, channel_id)
            if pair in writer_pairs:
                raise WorkflowSemanticError(
                    "duplicate native channel writer", details={"task": task}
                )
            writer_pairs.add(pair)
            writers.append(task)
            writer_count += 1
            if writer_count > _MAX_CHANNEL_WRITES:
                raise WorkflowSemanticError("native channel writer count exceeds the limit of 4096")
        try:
            validate_expression_data(item["initial"])
            initial_json = json.dumps(
                item["initial"],
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            raise WorkflowSemanticError("native channel initial value must be JSON-safe") from None
        if reducer == "append" and not isinstance(item["initial"], list):
            raise WorkflowSemanticError("append channel initial value must be an array")
        encoded_initial_bytes = len(initial_json.encode("utf-8"))
        initial_bytes += encoded_initial_bytes
        if encoded_initial_bytes > _MAX_CHANNEL_INITIAL_BYTES:
            raise WorkflowSemanticError("native channel initial value exceeds 65536 bytes")
        if initial_bytes > _MAX_CHANNEL_INITIAL_TOTAL_BYTES:
            raise WorkflowSemanticError("native channel initial data exceeds 262144 bytes")
        writers.sort(key=lambda reference: int(reference.split("/")[2]))
        channels.append(NativeChannel(channel_id, reducer, initial_json, tuple(writers)))

    known_channels = {channel.channel_id for channel in channels}
    reads: list[NativeChannelRead] = []
    reader_tasks: set[str] = set()
    read_count = 0
    for index, item in enumerate(read_items):
        reference = f"/document/metadata/owa/nativeExtensionData/{LANGGRAPH_CHANNELS}/reads/{index}"
        if not isinstance(item, Mapping) or set(item) != {"task", "as", "channels"}:
            raise WorkflowSemanticError(
                "native channel read must contain only task, as and channels",
                details={"reference": reference},
            )
        task = task_reference(item["task"], "native channel reader")
        if item["as"] != NATIVE_CHANNEL_VARIABLE:
            raise WorkflowSemanticError(
                f"native channel reads must use the reserved variable {NATIVE_CHANNEL_VARIABLE}",
                details={"reference": f"{reference}/as"},
            )
        if task in reader_tasks:
            raise WorkflowSemanticError("duplicate native channel reader", details={"task": task})
        reader_tasks.add(task)
        selected = item["channels"]
        if not isinstance(selected, list) or not selected:
            raise WorkflowSemanticError("native channel read channels must be a non-empty array")
        if any(
            not isinstance(channel, str) or channel not in known_channels for channel in selected
        ):
            raise WorkflowSemanticError("native channel read references an unknown channel")
        if len(set(selected)) != len(selected):
            raise WorkflowSemanticError("duplicate native channel read")
        reads.append(NativeChannelRead(task, tuple(selected)))
        read_count += len(selected)
        if read_count > _MAX_CHANNEL_WRITES:
            raise WorkflowSemanticError("native channel selection count exceeds the limit of 4096")

    # Check only OWS declarations that introduce expression-variable aliases;
    # arbitrary input/output objects may legitimately contain an `as` key.
    def check_aliases(value: Any) -> None:
        if isinstance(value, Mapping):
            loop = value.get("for")
            if isinstance(loop, Mapping) and (
                loop.get("each", "item") == NATIVE_CHANNEL_VARIABLE
                or loop.get("at", "index") == NATIVE_CHANNEL_VARIABLE
            ):
                raise WorkflowSemanticError(
                    "workflow collides with the reserved native channel variable"
                )
            iterator = value.get("foreach")
            if isinstance(iterator, Mapping) and (
                iterator.get("item", "item") == NATIVE_CHANNEL_VARIABLE
                or iterator.get("at", "index") == NATIVE_CHANNEL_VARIABLE
            ):
                raise WorkflowSemanticError(
                    "workflow collides with the reserved native channel variable"
                )
            catcher = value.get("catch")
            if (
                isinstance(catcher, Mapping)
                and catcher.get("as", "error") == NATIVE_CHANNEL_VARIABLE
            ):
                raise WorkflowSemanticError(
                    "workflow collides with the reserved native channel variable"
                )
            for child in value.values():
                check_aliases(child)
        elif isinstance(value, list):
            for child in value:
                check_aliases(child)

    check_aliases(workflow.get("do", []))

    return NativeChannelConfig(tuple(channels), tuple(reads))


def native_extension_requirements(
    workflow: Mapping[str, Any],
) -> tuple[NativeExtensionRequirement, ...]:
    """Read OWA's namespaced declarations from standard document metadata."""
    document = workflow.get("document")
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    owa = metadata.get("owa") if isinstance(metadata, Mapping) else None
    if owa is None:
        return ()
    if not isinstance(owa, Mapping):
        raise WorkflowSemanticError("document.metadata.owa must be an object")
    declarations = owa.get("nativeExtensions", [])
    if not isinstance(declarations, list):
        raise WorkflowSemanticError("document.metadata.owa.nativeExtensions must be an array")
    if len(declarations) > _MAX_DECLARATIONS:
        raise WorkflowSemanticError("native extension declarations exceed the limit of 32")
    requirements: list[NativeExtensionRequirement] = []
    seen: set[str] = set()
    for index, declaration in enumerate(declarations):
        reference = f"/document/metadata/owa/nativeExtensions/{index}"
        if not isinstance(declaration, Mapping) or set(declaration) - {
            "name",
            "version",
            "required",
        }:
            raise WorkflowSemanticError(
                "native extension declaration must contain only name, version and required",
                details={"reference": reference},
            )
        name, version = declaration.get("name"), declaration.get("version")
        required = declaration.get("required", True)
        if (
            not isinstance(name, str)
            or len(name) > _MAX_NAME_LENGTH
            or not _EXTENSION_NAME.fullmatch(name)
        ):
            raise WorkflowSemanticError(
                "native extension name must be a namespaced identifier",
                details={"reference": f"{reference}/name"},
            )
        if (
            not isinstance(version, str)
            or len(version) > _MAX_VERSION_LENGTH
            or not _EXTENSION_VERSION.fullmatch(version)
        ):
            raise WorkflowSemanticError(
                "native extension version must be an exact semantic version",
                details={"reference": f"{reference}/version"},
            )
        if type(required) is not bool:
            raise WorkflowSemanticError(
                "native extension required must be a boolean",
                details={"reference": f"{reference}/required"},
            )
        requirement = NativeExtensionRequirement(name, version, required)
        if requirement.capability_id in seen:
            raise WorkflowSemanticError(
                "duplicate native extension declaration", details={"reference": reference}
            )
        seen.add(requirement.capability_id)
        requirements.append(requirement)
    return tuple(requirements)


def verify_native_extension_requirements(
    requirements: tuple[NativeExtensionRequirement, ...],
    supported: tuple[str, ...] | frozenset[str],
) -> None:
    """Fail closed when this engine has not advertised an exact extension version."""
    capabilities = frozenset(supported)
    for requirement in requirements:
        if requirement.required and requirement.capability_id not in capabilities:
            raise UnsupportedWorkflowFeature(
                "required native workflow extension is not supported by the selected engine",
                details={"extension": requirement.capability_id},
            )
