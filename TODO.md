# Active Implementation Plan

`Project Definition.md` is authoritative. This backlog contains only work that still requires action; verified implementation history is summarized below.

## Verified Acceptance

- [x] Core, official Open Workflow 1.0.3 schema gate, Portable Profile gate, default workflow, shared semantics, API hardening, protocol security, observability, tools, and deterministic tests.
- [x] Independent ADK and LangGraph locked environments and multi-stage images with no startup installation.
- [x] Local container smoke acceptance for both engines: health, capabilities, FakeModel, mounts, arbitrary UID, read-only root, configured tools, and state externalization.
- [x] Local image knowledge acceptance for both engines: startup/manual/watch reload, deletion, unchanged-manifest reuse, packaged offline FastEmbed/ONNX search, and restart.
- [x] Genuine container stop -> restart -> resume -> complete for both engines with persisted native state, stable fingerprints, common status, and repeated side effects using the same idempotency key.

## Active Work (Dependency Order)

### A-001 — GitHub Actions and release gates (P0)

- [x] Add Ubuntu GitHub Actions jobs for root format/lint/mypy/tests/builds, both native + shared contract suites, independent Docker matrix builds, the 2 GiB image gate, health/readiness/capabilities, deterministic invocation, mounted knowledge, read-only root, and container logs.
- [ ] Run the workflow on GitHub for push and pull request and verify every job passes from a clean checkout; retain any failure logs/artifacts and fix discrepancies from local behavior.
- [ ] Verify release wheel contents, image tags/version metadata, and exact lock inputs in a completed remote run.
- Acceptance: the remote workflow is green and reproducible on GitHub-hosted Ubuntu runners.

### A-002 — Applicable Open Workflow CTK gate (P1; depends on A-001)

- [ ] Select CTK scenarios covered by Portable Profile v1, integrate the Gherkin scenarios and adapter harness, and run them beside contracts in CI.
- [ ] Keep public wording as an Open Workflow 1.0.3 based runtime supporting OWA Portable Profile v1 until the applicable scenarios pass.
- Acceptance: CTK results are reproducible and capabilities match actual coverage.

### A-003 — Configured persistence backend completeness (P2; specification gap)

- [ ] Implement and test `persistence.datasource` selection for invocation metadata, memory, knowledge metadata, and engine-owned persistence with separate namespaces; retain SQLite as the reference backend.
- Acceptance: configured datasource behavior is explicit, durable, isolated, and never shares one engine’s checkpoint representation with another.

### A-004 — Agent memory exposure (P2; specification gap)

- [ ] Expose common `MemoryService` to configured ADK and LangGraph agents through engine-native tool bindings while preserving the distinction between memory, knowledge, sessions, and checkpoints.
- [ ] Add deterministic cross-engine add/search/delete and persistence tests.
- Acceptance: both engines can use configured memory through the common contract without exposing framework-native state publicly.

## Deferred Future Candidates

`listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
