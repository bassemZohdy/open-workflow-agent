# Additional Engine Adapter Evaluation

Status: B-008 selection groundwork. No third engine dependency is enabled by this document.

## Decision criteria

A third engine must add architectural value rather than duplicate the existing ADK/LangGraph pair. The adapter must:

- support Python 3.12;
- be usable under a permissive open-source license;
- expose an explicit workflow/graph execution model suitable for compiling the existing immutable Open Workflow execution plan;
- support asynchronous execution and cancellation;
- have a credible persistence/checkpoint/resume story;
- keep model/provider choices optional rather than forcing one hosted provider;
- remain isolated in its own package, lock, and Docker image;
- avoid importing engine-native objects into `core`;
- pass the same shared contract, CTK, persistence, capability, and hardened-image gates before being advertised.

## Candidate 1 — Microsoft Agent Framework

Reference:

- https://github.com/microsoft/agent-framework
- https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/

Current Python package metadata identifies `agent-framework` / `agent-framework-core` as production/stable, Python >=3.10, and MIT licensed. The framework provides graph-based workflows, typed executors/edges, workflow events/state, checkpoints/resume, human-in-the-loop patterns, streaming/non-streaming execution, sub-workflows, and agent participation in workflows.

### Value to Open Workflow Agent

- validates that the common execution plan is not accidentally shaped only around ADK/LangGraph concepts;
- provides a third implementation with a different workflow runtime and checkpoint model;
- has first-class workflow/agent composition and current A2A-facing patterns, useful after B-007 boundaries are finalized;
- Python support aligns with the existing repository/toolchain;
- MIT licensing is compatible with an independent optional adapter package.

### Risks

- the umbrella package can pull a broad dependency graph; the adapter should depend on the smallest stable core/workflow packages that satisfy the SPI rather than `agent-framework[all]` if possible;
- Microsoft-specific provider integrations must remain optional and must not leak into the common model/tool contracts;
- framework-native durability/checkpoint identifiers must remain engine-owned state;
- functional workflow APIs are currently documented as experimental, so the adapter should prefer stable graph APIs.

## Candidate 2 — Pydantic AI / pydantic-graph

Reference:

- https://github.com/pydantic/pydantic-ai
- https://ai.pydantic.dev/graph/

Pydantic AI is MIT licensed and provides a typed agent runtime with broad provider support. `pydantic-graph` is a graph/state-machine library that can be installed independently and provides graph builders, async execution, decisions, parallel spread/broadcast, and joins.

### Value

- relatively natural fit with the repository's strict typed Python style;
- lightweight graph library can provide a clean compiler target;
- broad model support and strong typing.

### Risks

- closer conceptual overlap with LangGraph than Microsoft Agent Framework;
- `pydantic-graph` is primarily a code-defined graph/state-machine library, so it adds less independent production-runtime validation around durable workflow operations;
- using Pydantic AI itself may duplicate model/provider responsibilities already owned by the common runtime.

## Provisional selection

**Microsoft Agent Framework is the preferred B-008 third-engine candidate**, subject to a dependency/lock/image-size spike after B-006 and the B-007 boundary work are stable.

This is intentionally a selection decision only. Do not add the dependency or advertise the engine until the following gate is met:

1. B-005 production acceptance is recorded as green.
2. B-006 backend-neutral sandbox contract is stable.
3. B-007 A2A/streaming capability boundaries are finalized so engine-native streaming does not redefine the public contract.
4. A dependency spike proves an independent Microsoft Agent Framework adapter can remain below the project image-size gate without pulling unrelated Azure/provider packages.

## Adapter shape

When implementation starts, use the same repository pattern:

```text
engines/agent-framework/
├── pyproject.toml
├── uv.lock
└── src/open_workflow_agent_agent_framework/
```

and a separate runtime image:

```text
open-workflow-agent-agent-framework
```

The adapter must implement the existing `WorkflowEngine` SPI and compile the common `WorkflowPlan`; it must not introduce a second public workflow DSL or a third model/tool configuration contract.
