# Implementation Backlog

`Project Definition.md` is authoritative. Execute these milestones in order. A checkbox is complete only when its tests and acceptance criteria pass.

## Milestone 0 - Core Contracts

- [ ] Add strict configuration models.
- [ ] Add the Open Workflow loader.
- [ ] Validate against the official Open Workflow 1.0.3 JSON Schema without modifying it.
- [ ] Generate the default workflow when configuration omits one.
- [ ] Define the runtime catalog functions `agent:1.0.0@default` and `llm:1.0.0@default`.
- [ ] Define typed immutable execution-plan models and the normalizer.
- [ ] Define the engine SPI, runtime result models, and engine-neutral errors.
- [ ] Add framework-independent contract fixtures.
- [ ] Acceptance: a workflow loads into a validated `WorkflowPlan`, and minimal configuration produces an equivalent generated plan. No real engine execution is required.

## Milestone 1 - ADK Vertical Slice

- [ ] Add the ADK engine package and model adapter.
- [ ] Add the agent factory and `agent:1.0.0@default` catalog function.
- [ ] Add ADK 2.x dynamic workflow execution using native durable operations.
- [ ] Add `POST /v1/invoke` and health endpoints.
- [ ] Add deterministic `FakeModel` support.
- [ ] Add `docker/Dockerfile.adk` and an end-to-end test.
- [ ] Acceptance: model configuration -> generated default workflow -> ADK runtime -> `FakeModel` -> deterministic response.

## Milestone 2 - Portable Workflow Profile

- [ ] Implement common `do`, `call`, `set`, `switch`, `for`, and `fork` semantics.
- [ ] Implement `jq`, `input/from`, `output/as`, `export/as`, `http`, and `llm`.
- [ ] Make ADK pass all OWA Portable Profile v1 contract fixtures.
- [ ] Acceptance: every required Portable Profile v1 feature has passing ADK contract coverage.

## Milestone 3 - LangGraph Engine

- [ ] Add the LangGraph engine package using the Functional API for the generic executor.
- [ ] Add model and agent adapters, native checkpoint integration, and `docker/Dockerfile.langgraph`.
- [ ] Run the exact contract fixtures already passing on ADK.
- [ ] Acceptance: ADK and LangGraph produce identical results for all Portable Profile v1 workflows.

## Milestone 4 - Knowledge

- [ ] Add folder discovery, parsers, manifest hashing, chunking, embedding provider, and embedded persistent vector store.
- [ ] Add `search_knowledge`, reload endpoint, startup/manual reload, and watch/reconciliation.
- [ ] Keep retrieval in core; limit engine code to native tool wrappers.
- [ ] Acceptance: knowledge is tested against both images and unchanged documents are not re-embedded after restart.

## Milestone 5 - Persistence and Memory

- [ ] Add `InvocationStore`, `ExecutionHandle`, workflow fingerprints, persistent memory, and resume API.
- [ ] Add the ADK durable adapter and LangGraph persistent checkpointer/store integration.
- [ ] Keep public invocation metadata common while checkpoint representations remain engine-owned.
- [ ] Acceptance: execute -> stop container -> restart -> resume -> complete works for both engines where supported.

## Milestone 6 - Agent Tools

- [ ] Implement MCP and OpenAPI as configurable agent tools.
- [ ] Keep tool configuration external to workflow authoring and runtime images.
- [ ] Acceptance: adding or removing configured tools requires no image rebuild.

## Milestone 7 - Extended Workflow Calls

- [ ] Implement common MCP, A2A, and OpenAPI workflow-call behavior.
- [ ] Add `try`, `retry`, `timeout`, `wait`, and `raise` support to the portable workflow implementation.
- [ ] Acceptance: each newly portable capability has shared fixtures passing on every supported engine before it is advertised.

## Later Candidates

- [ ] Evaluate `listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines only after the defined milestones.
- [ ] Consider future OpenAI Agents SDK, Microsoft Agent Framework, or other engines without changing the public contract.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
