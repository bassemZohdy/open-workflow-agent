# Active Implementation Plan

`Project Definition.md` is authoritative. This file contains active acceptance work only; verified implementation history is summarized below, and deferred features remain separate. Do not mark a task complete until its stated acceptance evidence exists.

## Verified Baseline

The core contracts, official Open Workflow 1.0.3 schema and separate capability gate, default workflow, current Portable Profile semantics, ADK and LangGraph adapters, native process-level persistence/resume, knowledge indexing, deterministic embeddings, configured agent tools, protocol security, observability, API hardening, and shared contract coverage are implemented.

Current local evidence: root tests 65 passed/4 skipped; contract tests 32 passed; ADK tests 36 passed with one framework deprecation warning; LangGraph tests 36 passed; locked dependency checks, formatting, lint, type checking, root/core wheel builds, and schema packaging checks pass. No actual container acceptance has been verified.

## Active Work (Dependency Order)

### A-001 - CI, release, and container-runner gates (P0)

- [ ] Add reproducible CI jobs for root and standalone-core locked sync, lock checks, formatting, lint, mypy, root/core builds, root tests, contract tests, and both engine suites.
- [ ] Add a Linux container runner job that can build and run both images without paid model/API access; preserve build logs and test artifacts.
- [ ] Validate release artifacts by inspecting wheel contents, image tags/version metadata, and the exact locked dependency inputs.
- [ ] Acceptance: a clean checkout reproduces all non-container quality gates and provides an automated Docker-capable runner for the next tasks.

### A-002 - Independent ADK and LangGraph image acceptance (P0; depends on A-001)

- [ ] Build both images from their independent locked environments without runtime package installation.
- [ ] Run both with the same public configuration and `FakeModel`; verify liveness, readiness, capabilities, default invocation, graceful SIGTERM, and externally configured tools.
- [ ] Verify `/config`, `/knowledge`, and `/data` mounts, non-root/arbitrary-UID operation, read-only-root behavior where supported, and state externalization.
- [ ] Acceptance: both images pass container smoke/E2E tests with equivalent observable API behavior.

### A-003 - Image knowledge and local-embedding acceptance (P1; depends on A-002)

- [ ] Test mounted-folder startup, manual reload, periodic watch/reconciliation, deletion, and restart in both images.
- [ ] Prove unchanged documents retain their manifest and are not reparsed, re-embedded, or reindexed after restart.
- [ ] Verify the pinned CPU Sentence Transformers model is available locally in each image and `search_knowledge` returns equivalent results through both engines without a paid service.
- [ ] Acceptance: the complete knowledge milestone passes against both built images.

### A-004 - Container-native interrupted resume (P1; depends on A-002)

- [ ] Execute a deterministic side-effecting workflow, stop each container during execution, restart it with the same external state, resume through the common API, and complete it.
- [ ] Verify ADK session and LangGraph checkpoint restoration, stable workflow fingerprint checks, repeated side effects with the same idempotency key, and common status/error handling.
- [ ] Acceptance: execute -> stop -> restart -> resume -> complete passes for both engines where supported.

### A-005 - Applicable Open Workflow CTK gate (P2; depends on A-002 through A-004)

- [ ] Select the CTK scenarios covered by Portable Profile v1, integrate the applicable Gherkin scenarios and adapter harness, and run them in CI beside local contracts.
- [ ] Keep public wording as `Open Workflow 1.0.3 based runtime` / `supports OWA Portable Profile v1` until the relevant scenarios pass.
- [ ] Acceptance: CTK results are reproducible and advertised capabilities match actual coverage.

### A-006 - Configured persistence backend completeness (P2; specification gap)

- [ ] Implement and test `persistence.datasource` selection for invocation metadata, memory, knowledge metadata, and engine-owned persistence using separate namespaces; preserve SQLite as the local reference backend.
- [ ] Acceptance: configured datasource behavior is explicit, durable, isolated by subsystem, and does not make one engine consume another engine's checkpoint representation.

### A-007 - Agent memory exposure (P2; specification gap)

- [ ] Expose the common `MemoryService` to configured ADK and LangGraph agents through engine-native tool bindings while preserving the distinction between memory, knowledge, sessions, and checkpoints.
- [ ] Add deterministic cross-engine add/search/delete and persistence tests.
- [ ] Acceptance: both engines can use configured memory through the common contract without exposing framework-native state publicly.

## Deferred Future Candidates

- `listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines.
- OpenAI Agents SDK, Microsoft Agent Framework, or other engines.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
