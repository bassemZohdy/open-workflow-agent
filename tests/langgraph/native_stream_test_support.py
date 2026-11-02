"""Deterministic original-principal native streaming configuration."""

from open_workflow_agent.config import (
    NativeStateBranchConfig,
    NativeStateConfig,
    NativeStateReplayConfig,
    NativeStateUpdateConfig,
    RuntimeConfig,
)
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.security import static_principal
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def configuration(*, denied=False, **bounds):
    actions = ["invocations.create", "invocations.resume", "catalog.call", "model.complete"]
    if not denied:
        actions.append("invocations.stream")
    return RuntimeConfig.model_validate(
        {
            "server": {"api_security_profile": "actor-alice"},
            "security": {
                "profiles": {
                    "actor-" + actor: {
                        "type": "bearer",
                        "principal": actor,
                        "roles": ["observer"],
                        "scopes": ["observe"],
                        "audience": ["runtime"],
                        "token": {"from_env": "OWA_NATIVE_STREAM_TEST_" + actor.upper()},
                    }
                    for actor in ("alice", "bob")
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "native-stream-tests",
                "version": "1",
                "policy": {
                    "rules": [
                        {
                            "actions": actions,
                            "resources": ["*"],
                            "roles": ["observer"],
                            "scopes": ["observe"],
                            "audience": ["runtime"],
                        }
                    ]
                },
            },
            "observability": {"native_streams": bounds},
        }
    )


def authority(services, actor):
    profile = "actor-" + actor
    return ExecutionAuthority.capture(
        static_principal(services.config.security.profile(profile)), security_profile=profile
    )


def enable_lineage(config, *, branch=False):
    """Install the same required capabilities used by private lineage fixtures."""
    bounds = NativeStateConfig(max_native_bytes=4_194_304)
    config.native_state = bounds
    config.native_state_replay = NativeStateReplayConfig(bounds=bounds)
    if branch:
        config.native_state_updates = NativeStateUpdateConfig(bounds=bounds)
        config.native_state_branch = NativeStateBranchConfig(bounds=bounds)


def workflow(
    *,
    required=True,
    declared=True,
    waiting=False,
    task_stream=None,
    nested=False,
    checkpoint_stream=None,
    debug_stream=None,
    custom_stream=False,
):
    definition = generate_default_workflow()
    definition["document"]["name"] = "native-stream-test"
    if declared:
        definition["document"]["metadata"] = {
            "owa": {
                "nativeExtensions": [
                    {
                        "name": "langgraph.streams",
                        "version": "1.0.0",
                        "required": required,
                    }
                ]
            }
        }
        if task_stream is not None:
            definition["document"]["metadata"]["owa"]["nativeExtensions"].append(
                {"name": "langgraph.streams.tasks", "version": "1.0.0", "required": task_stream}
            )
        if checkpoint_stream is not None:
            definition["document"]["metadata"]["owa"]["nativeExtensions"].extend(
                [
                    {"name": "langgraph.state.read", "version": "1.0.0"},
                    {
                        "name": "langgraph.streams.checkpoints",
                        "version": "1.0.0",
                        "required": checkpoint_stream,
                    },
                ]
            )
        if debug_stream is not None:
            definition["document"]["metadata"]["owa"]["nativeExtensions"].append(
                {"name": "langgraph.streams.debug", "version": "1.0.0", "required": debug_stream}
            )
        if custom_stream:
            owa = definition["document"]["metadata"]["owa"]
            owa["nativeExtensions"].append({"name": "langgraph.streams.custom", "version": "1.0.0"})
            offset = 1 if nested else 0
            last = offset + (3 if waiting else 2)
            owa.setdefault("nativeExtensionData", {})["langgraph.streams.custom@1.0.0"] = {
                "events": [
                    {"task": f"/do/{offset}", "name": "before", "data": {"literal": "start"}},
                    {"task": f"/do/{last}", "name": "after", "data": {"from": "task_output"}},
                ]
            }
    definition["do"] = [
        *([{"outer": {"do": [{"child": {"set": {"value": "${ .value }"}}}]}}] if nested else []),
        {"first": {"set": {"value": "${ .value }"}}},
        {"answer": {"call": "llm:1.0.0@default"}},
        *([{"pause": {"wait": {"seconds": 300}}}] if waiting else []),
        {"last": {"set": {"done": True}}},
    ]
    return compile_workflow(definition)


def subgraph_waiting_workflow():
    import copy

    source = copy.deepcopy(workflow().source)
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.subgraphs", "version": "1.0.0"}
    )
    source["do"] = [
        {"outer": {"do": [
            {"call": {"call": "llm:1.0.0@default"}},
            {"pause": {"wait": {"seconds": 300}}},
            {"childlast": {"set": {"child": True}}},
        ]}},
        {"last": {"set": {"done": True}}},
    ]
    return compile_workflow(source)


def repeated_subgraph_waiting_workflow():
    import copy

    source = copy.deepcopy(workflow().source)
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.subgraphs", "version": "1.0.0"}
    )
    source["do"] = [
        {"first": {"set": {"count": 0}}},
        {"outer": {"do": [
            {"call": {"call": "llm:1.0.0@default"}},
            {"pause": {"wait": {"seconds": 300}}},
            {"increment": {"set": {"count": "${ .response.count + 1 }"}}},
        ]}},
        {"route": {"switch": [{"again": {"when": "${ .count < 2 }", "then": "outer"}}]}},
        {"last": {"set": {"done": True}}},
    ]
    return compile_workflow(source)
