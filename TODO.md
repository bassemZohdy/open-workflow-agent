# Implementation Backlog

`Project Definition.md` is authoritative. Execute these milestones in order. A checkbox is complete only when its tests and acceptance criteria pass.

## Milestone 0 - Core Contracts

- [x] Add strict configuration models.
- [x] Add the Open Workflow loader.
- [ ] Validate against the official Open Workflow 1.0.3 JSON Schema without modifying it. (Current implementation has the Portable Profile structural gate; full upstream schema vendoring remains.)
- [x] Generate the default workflow when configuration omits one.
- [x] Define the runtime catalog functions `agent:1.0.0@default` and `llm:1.0.0@default`.
- [x] Define typed immutable execution-plan models and the normalizer.
- [x] Define the engine SPI, runtime result models, and engine-neutral errors.
- [x] Add framework-independent contract fixtures.
- [x] Acceptance: a workflow loads into a validated `WorkflowPlan`, and minimal configuration produces an equivalent generated plan. No real engine execution is required.

## Milestone 1 - ADK Vertical Slice

- [x] Add the ADK engine package and model adapter.
- [x] Add the agent factory and `agent:1.0.0@default` catalog function.
- [x] Add ADK 2.x dynamic workflow execution using native durable operations (`Context.run_node` with SQLite session persistence).
- [x] Add `POST /v1/invoke` and health endpoints.
- [x] Add deterministic `FakeModel` support.
- [x] Add `docker/Dockerfile.adk` and an end-to-end test.
- [x] Acceptance: model configuration -> generated default workflow -> ADK runtime -> `FakeModel` -> deterministic response, including native restart/resume coverage.

## Milestone 2 - Portable Workflow Profile

- [x] Implement common `do`, `call`, `set`, `switch`, `for`, and `fork` semantics.
- [x] Implement the tested `jq` subset, `input/from`, `output/as`, `export/as`, `http`, and `llm`.
- [x] Make the ADK adapter pass the current OWA Portable Profile v1 contract fixtures.
- [x] Acceptance: every currently implemented Portable Profile v1 fixture has passing adapter coverage.

## Milestone 3 - LangGraph Engine

- [x] Add the LangGraph engine package and an optional Functional API bridge for the generic executor.
- [x] Add model and agent adapters, an in-memory native checkpointer bridge, and `docker/Dockerfile.langgraph`.
- [x] Run the shared contract fixtures through both adapter boundaries.
- [x] Acceptance: ADK and LangGraph produce identical results for the current Portable Profile v1 workflows.
- [x] Replace the deterministic reference path with native engine compilation/checkpoint execution where framework dependencies are available. (ADK 2.7.1 dynamic node and LangGraph Functional API paths are covered; fallback remains for core-only environments.)

## Milestone 4 - Knowledge

- [x] Add folder discovery, parsers, manifest hashing, chunking, embedding provider, and embedded persistent vector store.
- [x] Add `search_knowledge`, reload endpoint, startup/manual reload, and watch/reconciliation.
- [x] Keep retrieval in core; limit engine code to configured/native tool wrappers.
- [ ] Acceptance: knowledge is tested against both images and unchanged documents are not re-embedded after restart. (Core restart/manifest coverage passes; Docker image validation is pending the local daemon.)

## Milestone 5 - Persistence and Memory

- [x] Add `InvocationStore`, `ExecutionHandle`, workflow fingerprints, persistent memory, and resume API.
- [x] Add the ADK durable adapter and LangGraph native SQLite checkpointer bridge.
- [x] Keep public invocation metadata common while checkpoint representations remain engine-owned.
- [ ] Acceptance: execute -> stop container -> restart -> resume -> complete works for both engines where supported. (ADK and LangGraph SQLite restart/resume tests pass; container validation remains.)

## Milestone 6 - Agent Tools

- [x] Implement MCP and OpenAPI as configurable agent tools.
- [x] Keep tool configuration external to workflow authoring and runtime images.
- [x] Acceptance: adding or removing configured tools requires no image rebuild.

## Milestone 7 - Extended Workflow Calls

- [x] Implement common HTTP-backed MCP, A2A, and OpenAPI workflow-call behavior.
- [x] Add `try`, `retry`, `timeout`, `wait`, and `raise` support to the portable workflow implementation.
- [x] Acceptance: current newly implemented capabilities have shared deterministic coverage through both adapter boundaries.

## Later Candidates

- [ ] Evaluate `listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines only after the defined milestones.
- [ ] Consider future OpenAI Agents SDK, Microsoft Agent Framework, or other engines without changing the public contract.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
