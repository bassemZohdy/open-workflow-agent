# Implementation Backlog

`Project Definition.md` is authoritative. This file records verified progress and the remaining work needed to satisfy its milestones and acceptance criteria. Complete backlog items in the listed order; a checkbox is complete only after code, tests, and the stated acceptance check pass.

## Verified Completed Scope

- [x] **Milestone 0 — Core contracts:** strict configuration, workflow loading/default generation, the unmodified official Open Workflow 1.0.3 schema, catalog functions, immutable plans/fingerprints, engine SPI/errors, and deterministic fixtures are implemented. Capability validation remains a separate Portable Profile gate.
- [x] **Milestone 1 — ADK vertical slice:** ADK package/model and agent adapters, default agent catalog function, native dynamic execution, API/health endpoints, `FakeModel`, Dockerfile, and deterministic E2E coverage are present. Native SQLite restart tests pass; container acceptance remains in `B-002` and `B-004`.
- [x] **Milestone 2 — Portable Profile implementation:** common task execution for `do`, `call`, `set`, `switch`, `for`, `fork`, the current `jq` subset, data mappings, HTTP/LLM calls, retry/timeout behavior, input/output schemas, and shared fixture parity are verified.
- [x] **Milestone 3 — LangGraph engine:** LangGraph adapter, Functional API bridge, optional SQLite checkpointer, shared adapter execution, and native tests are present. Correct independent image packaging remains in `B-002`.
- [x] **Milestone 4 — Knowledge implementation:** discovery, parsing, manifesting, chunking, a pinned local embedding provider, SQLite vector search, reload, and reconciliation/watch support are present. Built-image acceptance remains in `B-003` and `B-006`.
- [x] **Milestone 5 — Persistence and memory implementation:** invocation metadata, execution handles, fingerprints, SQLite memory, resume API, ADK durability, and LangGraph SQLite bridging are present. True interrupted-container resume acceptance remains in `B-004`.
- [x] **Milestone 6 — Agent tool configuration:** external MCP/OpenAPI/A2A tool definitions, common protocol registry, bounded tool-call execution, and ADK/LangChain-native bindings are present. Built-image acceptance remains in `B-002`.
- [x] **Milestone 7 — Extended workflow-call implementation:** common HTTP-backed MCP/A2A/OpenAPI calls and `try`, `retry`, `timeout`, `wait`, and `raise` handlers are present with shared deterministic coverage and security tests.

## Remaining Implementation Backlog

### B-001 — Vendor and enforce the official Open Workflow 1.0.3 schema (P0; complete)

- [x] Add the unmodified upstream 1.0.3 schema at `resources/open-workflow/1.0.3/workflow.yaml`; record its source, version, and integrity metadata.
- [x] Load that schema from the resource path in `SchemaValidator`; keep capability validation as a separate post-schema gate.
- [x] Add valid and invalid schema fixtures, including proof that proprietary task/call keywords are not added to the schema.
- [x] Acceptance: schema-valid documents produce plans, schema-invalid documents raise `WorkflowSchemaError`, and unsupported-but-schema-valid features raise `UnsupportedWorkflowFeature`.

### B-002 — Make the two engine images independently runnable (P0)

- [x] Make the root/core dependency set framework-neutral; keep ADK and LangGraph dependencies in their engine packages and independent locks. Root/core and engine dependencies are exact-pinned and independently locked.
- [x] Update each Dockerfile to install core plus its matching engine package and native extras, rather than relying on source `PYTHONPATH` and the root package’s incidental dependencies.
- [x] Externalize `/config`, `/knowledge`, and `/data`; use writable paths compatible with non-root arbitrary UIDs, read-only roots where practical, readiness/liveness, and graceful termination. Dockerfiles provision the external paths and run as a non-root arbitrary-UID-compatible user; runtime validation remains below.
- [ ] Build and run both images with `FakeModel`, verify health, default invocation, capabilities, and externally configured tools without runtime package installation.
- [ ] Acceptance: both images build and pass container smoke/E2E tests with the same public configuration and no paid model/API access.

### B-003 — Complete knowledge acceptance in both images (P1; depends on B-002)

- [ ] Run startup, manual reload, watch/reconciliation, deletion, and restart tests with a mounted knowledge directory in each image.
- [ ] Verify unchanged files retain their manifest and are not parsed, embedded, or indexed again after restart.
- [x] Verify the configured `search_knowledge` tool returns equivalent results through both engine adapters with deterministic mounted-folder fixtures. Built-image verification remains in the acceptance item.
- [ ] Acceptance: the knowledge milestone passes against both built images.

### B-004 — Implement genuine interrupted-execution resume (P1; depends on B-002)

- [x] Add deterministic ADK and LangGraph pause/stop fixtures that leave an invocation running, close/reopen the persistence service, resume with the native engine identity, and verify repeated side effects carry the same idempotency key. Container-level acceptance remains below.
- [ ] Persist and restore engine-native ADK session and LangGraph checkpoint state across container restart while keeping only common invocation metadata public. Process/service close-and-reopen coverage now exists for both native adapters; the container boundary remains below.
- [x] Verify workflow fingerprint mismatch, repeated side effects, and resume status/error handling for the common API and interrupted ADK/LangGraph paths; container-level proof remains below.
- [ ] Acceptance: execute → stop container → restart → resume → complete passes for both engines where supported.

### B-005 — Finish Portable Profile and cross-engine contract coverage (P1)

- [x] Add shared fixtures for the specification’s missing examples: sequence, LLM call, data transformation, controlled error, input validation, nested task references, task/workflow `input.from`, `output.as`, `export.as`, and workflow output.
- [x] Add deterministic shared fixtures for HTTP, MCP, A2A, OpenAPI, `try`, `retry`, `timeout`, `wait`, and `raise` behavior.
- [x] Extend `FakeModel` tests and adapters for simple output, structured output, tool requests, controlled failures, and controlled retries.
- [x] Run every fixture through ADK and LangGraph and compare the same expected observable result and common error contract.
- [x] Acceptance: the complete currently claimed Portable Profile has identical adapter results; capabilities do not claim untested features.

### B-006 — Replace the provisional knowledge embedding implementation (P1)

- [x] Select and pin a small permissively licensed local CPU embedding model, with a deterministic/offline test fixture and documented model/version. The default is `sentence-transformers/all-MiniLM-L6-v2` (Apache-2.0) at revision `ea78891063587eb050ed4166b20062eaf978037c`; the `sentence-transformers==5.7.0` package is locked.
- [x] Preserve the `EmbeddingProvider` abstraction and manifest metadata; do not make model choice part of `KnowledgeService`’s public API. Deterministic embeddings remain injectable for offline tests.
- [ ] Acceptance: mounted-folder knowledge works without a paid service and unchanged documents are not re-embedded after restart.

### B-007 — Expose configured tools as real engine-native agent tools (P1)

- [x] Bind configured MCP/OpenAPI/A2A definitions to ADK tools and LangGraph/LangChain tools, not only to the prompt’s tool-name list.
- [x] Add deterministic FakeModel tool-request tests and verify tool results flow back through the agent function.
- [x] Verify adding/removing tool configuration changes runtime behavior without rebuilding either image. Engine initialization binds the current external configuration into native tool objects.
- [x] Acceptance: agent tools and explicit workflow calls remain separate and both engine adapters execute configured tools equivalently.

### B-008 — Add common lifecycle events and task-level observability (P1)

- [x] Define common `WorkflowStarted`, `WorkflowCompleted`, `WorkflowFaulted`, `TaskStarted`, `TaskCompleted`, `TaskFaulted`, and `TaskRetried` events.
- [x] Emit structured events/log records containing invocation/session IDs, workflow name/version, task name/reference, engine, engine execution reference, duration, status, and error details without secrets.
- [x] Add tests for successful, faulted, retried, and resumed executions; keep CloudEvents exposure deferred.
- [x] Acceptance: every executed task is traceable by its canonical Open Workflow reference across both engines.

### B-009 — Harden the public API and runtime lifecycle (P1)

- [x] Enforce `server.max_request_bytes` for invocation and resume payloads; ensure configured host/port are used by each launcher.
- [x] Implement meaningful readiness checks for configuration, knowledge startup, persistence, and engine initialization; preserve liveness independently.
- [x] Standardize faulted invocation, resume, validation, and not-found responses on the common error contract, with tests for generated sessions and user correlation data.
- [x] Acceptance: API behavior matches the stable HTTP contract and remains safe under malformed/oversized input.

### B-010 — Complete protocol security and side-effect handling (P1)

- [x] Add an authentication abstraction to common HTTP/MCP/A2A/OpenAPI clients without allowing ordinary workflow files to contain deployment secrets.
- [x] Add configurable host/egress policy hooks, explicit timeout/redirect/TLS/response-size tests, and safe error translation for all protocol clients.
- [x] Carry operation identifiers/idempotency keys through retryable side-effecting calls and document at-least-once resume/retry behavior.
- [x] Acceptance: protocol clients satisfy the security requirements and retry/resume tests cannot silently claim exactly-once execution.

### B-011 — Add the Open Workflow CTK compatibility gate (P2; depends on B-001 and B-005)

- [ ] Select the applicable CTK scenarios for the Portable Profile and run them in CI alongside the local contract suite.
- [ ] Keep public wording as “Open Workflow 1.0.3 based runtime” / “supports OWA Portable Profile v1” until the relevant CTK scenarios pass.
- [ ] Acceptance: CTK results are reproducible and the advertised capabilities match actual coverage.

## Deferred Later Candidates

- [ ] After the current backlog, evaluate `listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines.
- [ ] Consider OpenAI Agents SDK, Microsoft Agent Framework, or other engines without changing the public contract or moving checkpointing into core.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
