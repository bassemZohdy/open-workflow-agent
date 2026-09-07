# Decision Analysis Report — ADK vs LangChain vs LangGraph

**Date:** 2026-09-07
**Repository:** `open-workflow-agent`
**Commit audited:** `37fcd9e` (`main`)
**Decision scope:** implementation fit for the current Open Workflow Agent architecture

## Executive decision

**If one production engine must be preferred, select LangGraph with a score of 88.0/100.** It has the more direct native checkpoint/resume path, uses a real LangGraph Functional API entrypoint, has native `Command(resume=...)` handling, and produces the smaller documented runtime image.

**Keep ADK as a supported production engine, at 83.5/100.** It passes the same portable and native acceptance gates and provides a credible ADK Runner/session persistence integration. Its main weakness is that its resume method delegates to a new invocation with `rerun_on_resume=True`, so the repository evidence proves recovery behavior but not true native continuation at every workflow boundary.

**Do not treat LangChain as a third engine.** The repository has no `engines/langchain` package, no LangChain server/image, no LangChain engine class, and no LangChain-specific workflow or checkpoint implementation. `langchain_core.StructuredTool` is used incidentally by the LangGraph tool adapter. Its implementation score is therefore **0.0/100**, not because the LangChain ecosystem lacks value, but because no standalone LangChain runtime has been implemented here.

The practical architecture decision is therefore:

```text
Production engines: ADK + LangGraph
Preferred native-runtime edge: LangGraph
LangChain role: building-block dependency/tool interoperability, not a standalone OWA engine
```

This is a comparative implementation score, not a claim of full Open Workflow, A2A, or framework conformance. The project itself limits claims to the tested Portable Profile and bounded protocol profiles.

## Scope and interpretation

The audit compares three labels requested by the user:

| Label | Repository implementation | What is actually scored |
| --- | --- | --- |
| ADK | `engines/adk/` | `AdkWorkflowEngine`, `NativeAdkRunner`, ADK tool binding, ADK session persistence |
| LangGraph | `engines/langgraph/` | `LangGraphWorkflowEngine`, Functional API bridge, LangGraph checkpointing, LangChain Core tool binding |
| LangChain | No standalone adapter | Current repository evidence only; no credit is given for capabilities that are not wired into OWA |

The word “LangChain” is especially important here. LangGraph depends on `langchain-core`, but LangGraph is the workflow/runtime adapter in this project. A future LangChain-based adapter would be a new implementation and would require its own package, engine class, persistence strategy, image, capability evidence, and tests.

## Governing architecture

The project definition requires:

- Open Workflow Specification 1.0.3 to remain the only public DSL.
- Every invocation to execute through a workflow, including the generated default workflow.
- One immutable, typed, internal execution plan to feed each engine.
- Core to own loading, validation, normalization, `jq`/data semantics, catalog resolution, common services, lifecycle, errors, and protocol boundaries.
- Engine adapters to own framework-native construction, execution, checkpoint/session integration, callbacks, and tool wrapping.
- Portability to be demonstrated by shared fixtures and expected results, not by documentation alone.
- Executable workflow operations to use the common `SandboxManager`; no engine-owned subprocess, Docker, or Kubernetes path.

These rules make shared-core parity a deliberate product feature. They also mean a native framework score must separately ask how much of the framework is genuinely used beyond the common executor.

Evidence: [Project Definition.md — canonical plan and engine SPI](../Project%20Definition.md#L185-L306), [Project Definition.md — native durability and model/agent boundaries](../Project%20Definition.md#L307-L438), [Project Definition.md — testing strategy](../Project%20Definition.md#L900-L925).

## Current implementation map

### Common core path

`compile_workflow()` loads or generates the default Open Workflow document, validates the official schema and capabilities, and normalizes it into the immutable `WorkflowPlan`. `WorkflowEngine.compile()` currently returns that plan unchanged. The `PortableWorkflowEngine` creates the common `SandboxWorkflowExecutor`, and both production adapters subclass it.

The common catalog owns the default `agent:1.0.0@default` and `llm:1.0.0@default` functions. The default agent loop calls the common `Model` contract and common tool service; it is not an ADK Agent loop or a LangGraph node/tool-call loop. This is correct for portability, but it limits the amount of native framework behavior exercised by either adapter.

Evidence: [core engine SPI](../core/src/open_workflow_agent/engine.py#L102-L184), [workflow compilation and immutable plan](../core/src/open_workflow_agent/workflow.py#L237-L319), [common workflow executor](../core/src/open_workflow_agent/workflow.py#L564-L649), [common catalog agent/LLM functions](../core/src/open_workflow_agent/catalog.py#L123-L184).

### ADK path

The ADK adapter:

- subclasses `PortableWorkflowEngine`;
- falls back to the common executor when the optional native package is unavailable;
- otherwise invokes `NativeAdkRunner` around the common executor;
- uses ADK `Runner`, `FunctionNode`, `Context.run_node()`, and `run_async()`;
- persists ADK sessions through SQLite or the ADK database session service for PostgreSQL;
- wraps common tool bindings as ADK `FunctionTool` objects;
- creates an `AdkAgentSpec`, not a native ADK Agent object;
- reports the same `resume=True`, `streaming=False`, cancellation, and waiting capabilities as the common contract.

The native boundary is real, but shallow: the plan is not compiled into one ADK node per Open Workflow task. Instead, the entire common plan executor runs inside a dynamic ADK node.

Evidence: [ADK engine](../engines/adk/src/open_workflow_agent_adk/__init__.py#L14-L67), [ADK native runner](../engines/adk/src/open_workflow_agent_adk/native.py#L12-L138), [ADK resume bridge](../engines/adk/src/open_workflow_agent_adk/native.py#L140-L158), [ADK tool factory](../engines/adk/src/open_workflow_agent_adk/agent.py#L12-L42).

### LangGraph path

The LangGraph adapter:

- subclasses `PortableWorkflowEngine`;
- wraps the common executor in a LangGraph Functional API `@entrypoint`;
- uses `InMemorySaver`, `AsyncSqliteSaver`, or `AsyncPostgresSaver` depending on deployment;
- invokes through `graph.ainvoke()` with a `thread_id`;
- resumes through `Command(resume=...)` with the same thread identity;
- wraps common tool bindings as LangChain Core `StructuredTool` objects;
- creates a `LangGraphAgentSpec`, not a native LangGraph agent/graph for each Open Workflow task;
- reports the same common public capabilities as ADK.

This is a slightly stronger native durability bridge than ADK because the resume path explicitly uses LangGraph’s `Command(resume=...)`. The workflow plan is still not compiled into a task-level LangGraph topology; the native graph is an envelope around the common executor.

Evidence: [LangGraph engine](../engines/langgraph/src/open_workflow_agent_langgraph/__init__.py#L14-L61), [LangGraph Functional API and checkpoint bridge](../engines/langgraph/src/open_workflow_agent_langgraph/native.py#L10-L105), [LangGraph tool factory](../engines/langgraph/src/open_workflow_agent_langgraph/agent.py#L12-L46).

### LangChain path

There is no standalone LangChain implementation. The current repository contains no `engines/langchain/` package, no `LangChainWorkflowEngine`, no LangChain runtime image, and no LangChain-specific tests. `langchain-core` appears only as the tool type used by the LangGraph adapter, while the model contract remains OWA’s common `Model`/LiteLLM abstraction.

That makes LangChain a potential future integration layer, not a current engine choice. LangChain alone would also need a workflow execution and durability design to satisfy OWA’s engine SPI; using LangGraph alongside LangChain is the current repository’s solution.

## Weighted scoring method

Each criterion is scored from 0 to 5:

| Score | Meaning |
| --- | --- |
| 5 | Strong implementation evidence, aligned with the project contract, and verified by applicable gates |
| 4 | Production-capable evidence with a material but bounded caveat |
| 3 | Partial/native bridge is implemented, but important framework behavior remains in common core or is not fully proven |
| 2 | Thin or mostly representational integration |
| 1 | Incidental use only or largely unverified |
| 0 | Not implemented in this repository |

Weighted points are calculated as:

```text
weighted points = criterion weight × (raw score / 5)
total score = sum of weighted points
```

The weights prioritize the OWA product contract over framework feature breadth.

## Full weighted scorecard

| Criterion | Weight | ADK raw /5 | ADK points | LangChain raw /5 | LangChain points | LangGraph raw /5 | LangGraph points |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1. Public contract fidelity and portability | 20 | 5.0 | 20.0 | 0.0 | 0.0 | 5.0 | 20.0 |
| 2. Open Workflow 1.0.3 semantics coverage | 20 | 5.0 | 20.0 | 0.0 | 0.0 | 5.0 | 20.0 |
| 3. Native workflow execution integration | 15 | 3.0 | 9.0 | 0.0 | 0.0 | 3.5 | 10.5 |
| 4. Durability, resume, and cancellation | 15 | 3.5 | 10.5 | 0.0 | 0.0 | 4.5 | 13.5 |
| 5. Agent, model, and tool integration | 10 | 2.5 | 5.0 | 0.0 | 0.0 | 2.5 | 5.0 |
| 6. Security, sandbox, protocol, and lifecycle boundaries | 10 | 5.0 | 10.0 | 0.0 | 0.0 | 5.0 | 10.0 |
| 7. Operations, dependency isolation, and verification | 10 | 4.5 | 9.0 | 0.0 | 0.0 | 4.5 | 9.0 |
| **Total** | **100** |  | **83.5** |  | **0.0** |  | **88.0** |

### Criterion 1 — Public contract fidelity and portability: 20%

**ADK: 5/5. LangGraph: 5/5. LangChain: 0/5.**

Both production adapters inherit the common engine implementation, consume the same `WorkflowPlan`, preserve public invocation identity, and leave core framework-neutral. The shared contract suite and CTK subset exercise both engines with the same fixtures. The equal score is intentional: this category measures adherence to OWA’s public contract, not framework-specific richness.

LangChain receives zero because no adapter exposes the OWA engine SPI.

### Criterion 2 — Open Workflow 1.0.3 semantics coverage: 20%

**ADK: 5/5. LangGraph: 5/5. LangChain: 0/5.**

The portable executor implements the declared Portable Profile tasks, functions, protocol boundaries, policies, nested workflows, event operations, and sandbox routing. Both production engines execute that same semantics path. The CTK README records 22 feature files, 42 scenarios, and 84 deterministic executions across ADK and LangGraph, while explicitly avoiding a full Open Workflow conformance claim.

The score does not mean full DSL conformance. It means both engines have equivalent evidence for the current declared subset.

Evidence: [CTK scope and limits](../tests/ctk/README.md#L3-L28).

### Criterion 3 — Native workflow execution integration: 15%

**ADK: 3/5. LangGraph: 3.5/5. LangChain: 0/5.**

ADK uses genuine ADK runtime objects (`Runner`, `FunctionNode`, `Context`, and asynchronous event iteration), but the complete plan is executed by the common executor inside a dynamic node. The adapter does not compile individual Open Workflow tasks into ADK-native nodes.

LangGraph uses a genuine Functional API entrypoint and calls the graph natively. It has a somewhat cleaner native function boundary and native resume command, so it gets a half-point advantage. It also does not compile the complete `WorkflowPlan` into task-level graph nodes; the native graph remains a wrapper around the common executor.

This is the largest architectural limitation in both implementations. The current code is a reliable native execution envelope, not a deep framework compiler.

### Criterion 4 — Durability, resume, and cancellation: 15%

**ADK: 3.5/5. LangGraph: 4.5/5. LangChain: 0/5.**

ADK persists sessions through ADK’s SQLite session service and optional PostgreSQL database service. The native tests cover interruption, restart, resume, and preservation of the same idempotency key around a side effect. However, `NativeAdkRunner.resume()` delegates to `invoke()`, and the ADK root node is configured with `rerun_on_resume=True`. This can recover correctly while still replaying work; the tests do not prove exactly-once or task-boundary continuation.

LangGraph persists through its native saver interfaces and resumes with `Command(resume=...)` on the same thread id. The native tests cover interruption, restart, resume, and side-effect identity preservation. This is the clearest current native durability implementation, although side effects still require idempotency because checkpoints do not imply exactly-once execution.

Both engines inherit common cancellation and invocation state handling. Neither exposes native run/thread/checkpoint ids publicly.

Evidence: [ADK resume implementation](../engines/adk/src/open_workflow_agent_adk/native.py#L140-L158), [LangGraph checkpoint/resume implementation](../engines/langgraph/src/open_workflow_agent_langgraph/native.py#L51-L105), [common cancellation and resume state](../core/src/open_workflow_agent/engine.py#L153-L231).

### Criterion 5 — Agent, model, and tool integration: 10%

**ADK: 2.5/5. LangGraph: 2.5/5. LangChain: 0/5.**

Both adapters correctly bind common tool contracts to native tool wrapper types: ADK `FunctionTool` and LangChain Core `StructuredTool`. However, both factories create project-owned `*AgentSpec` dataclasses rather than native agent objects, and both model adapters simply delegate to OWA’s common `Model` contract. The actual default agent loop is implemented in the common catalog and calls common model/tool services.

This equal score is deliberate. LangGraph gets a native LangChain Core tool type, but that does not make the repository a LangChain engine; ADK gets an equivalent native ADK tool type. Neither adapter currently proves native framework agent-loop behavior.

Evidence: [common model and agent loop](../core/src/open_workflow_agent/catalog.py#L12-L73), [common tool binding composition](../core/src/open_workflow_agent/services.py#L201-L230).

### Criterion 6 — Security, sandbox, protocol, and lifecycle boundaries: 10%

**ADK: 5/5. LangGraph: 5/5. LangChain: 0/5.**

This is common-core functionality and both engines receive the same score. Protocol clients, security profiles, lifecycle events, common errors, knowledge, memory, approvals, schedules, and sandbox execution are wired through `RuntimeServices` and `SandboxManager`. The adapters do not create independent subprocess, Docker, Kubernetes, protocol, or public checkpoint paths.

Both engines report `streaming=False` at the engine capability level. The bounded lifecycle SSE and A2A streaming profile are common protocol observations/projections, not native token streams and not exposed framework stream objects.

Evidence: [runtime service composition](../core/src/open_workflow_agent/services.py#L42-L87), [engine-neutral capabilities](../core/src/open_workflow_agent/engine.py#L28-L79), [sandbox boundary](../docs/sandbox-execution.md#L54-L79).

### Criterion 7 — Operations, dependency isolation, and verification: 10%

**ADK: 4.5/5. LangGraph: 4.5/5. LangChain: 0/5.**

ADK and LangGraph each have separate package metadata, lock files, Dockerfiles, runtime images, native tests, shared contract tests, CTK tests, and container/restart acceptance in the project’s CI design. The documented image sizes are approximately 266 MB for ADK and 248 MB for LangGraph, both below the 2 GiB hard gate.

ADK’s native dependency is an optional `native` extra, which provides a useful fallback but allows a non-native deployment of the adapter. LangGraph declares `langgraph` as a direct dependency and makes SQLite checkpoint support an extra. These are operational differences, but neither is a release blocker under the current packaging model.

LangChain has no package, lock, image, server, test suite, or CI job in this repository.

Evidence: [ADK package metadata](../engines/adk/pyproject.toml#L1-L16), [LangGraph package metadata](../engines/langgraph/pyproject.toml#L1-L19), [deployment image and verification gates](../docs/deployment.md#L472-L493).

## Verification performed for this report

The following commands were run against the audited worktree:

| Verification | Result |
| --- | --- |
| `uv run --locked pytest tests/adk tests/langgraph tests/contract tests/ctk -q` | 168 passed, 4 skipped |
| ADK locked native/contract/CTK command from project instructions | 168 passed, 1 upstream deprecation warning |
| LangGraph locked SQLite/contract/CTK command from project instructions | 168 passed |
| `uv run --locked pytest -q --cov=core/src/open_workflow_agent --cov-fail-under=90` | 626 passed, 11 skipped, 90.24% coverage |

The native engine commands passed in this report’s environments; the root run’s skips are expected optional-dependency skips. The CTK suite remains a Portable Profile subset, not a full Open Workflow conformance suite.

## Risk and gap register

| ID | Finding | Impact | Priority |
| --- | --- | --- | --- |
| R1 | Native compilation is wrapper-level, not task-level. | Framework-specific graph topology, interrupts, native task streaming, and node-level state are not being exercised by OWA workflows. | High |
| R2 | ADK resume calls the invoke bridge and enables rerun on resume. | Recovery can replay side effects; idempotency is required and exactly-once is not established. | High |
| R3 | Both `*AgentFactory` classes return project-owned specs. | Native agent-loop, provider-native tool-call, and framework-native agent behavior are not proven. | Medium |
| R4 | Engine capability output is intentionally almost identical. | Consumers cannot infer the native durability distinction from the current public capability document. | Medium |
| R5 | No standalone LangChain adapter exists. | LangChain cannot be selected as a third OWA runtime without new architecture and acceptance work. | High if requested as a product choice |
| R6 | Performance evidence is common-core oriented. | The repository does not currently establish a fair native ADK-vs-LangGraph latency/throughput advantage. | Medium |

## Recommendation and next actions

1. **Choose LangGraph as the default engine for new native-runtime investment.** Its current checkpoint/resume bridge is the strongest differentiator and the documented image is smaller.

2. **Retain ADK as a first-class production option.** It has passed the same portability, native, persistence, container, and release gates; the score gap is an implementation-depth gap, not a failure of the OWA contract.

3. **Do not add a standalone LangChain engine solely to increase framework count.** If LangChain is needed, use LangChain Core types behind an adapter or add a real LangChain engine only with a defined workflow runtime, checkpoint/resume design, capability advertisement, independent lock/image, and the same shared fixtures.

4. **Decide whether to deepen or narrow the native claim.** The next architecture decision is either:

   - implement a genuine plan-to-native-node compiler for each production engine, with task-boundary checkpoint and cancellation tests; or
   - document the current adapters precisely as native execution envelopes around the common portable executor.

5. **Add engine-differentiating benchmarks and native behavior tests before changing the weighted decision.** Measure compilation, sequential invocation, concurrent throughput, native interrupt/resume, checkpoint growth, and side-effect replay behavior separately for ADK and LangGraph. The existing dependency-free benchmark primarily measures common runtime behavior and should not be used as a native-framework ranking.

## Final decision record

```text
Decision: LangGraph is the preferred single-engine default.
Status: ADK remains production-supported.
LangChain: not an implemented standalone engine; do not rank as equivalent.
Score: LangGraph 88.0, ADK 83.5, LangChain 0.0.
Blocking caveat: native task-level compilation and stronger resume/replay evidence remain future work.
```
