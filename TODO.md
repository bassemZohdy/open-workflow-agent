# Active Implementation Plan

`Project Definition.md` is authoritative. This file contains only active work and a concise verified summary.

## Verified Milestones

- [x] Framework-neutral core, official Open Workflow 1.0.3 schema validation, Portable Profile gate, shared semantics, API hardening, protocol security, observability, tools, and deterministic tests.
- [x] Independently packaged ADK and LangGraph engines with exact locks, native persistence, multi-stage images, non-root execution, and no startup installation.
- [x] FastEmbed/ONNX offline knowledge support with mounted-folder reload, deletion, unchanged-manifest reuse, restart, and deterministic test providers.
- [x] Local container acceptance for health/readiness, capabilities, FakeModel invocation, mounts, arbitrary UID, read-only root, configured tools, state externalization, and genuine stop/restart/resume for both engines.
- [x] Common memory tools with cross-engine add/search/delete and persistence coverage.
- [x] Pinned applicable CTK subset (`do`, `set`, `switch`, `for`) integrated with both engine contract suites and passing locally.

## Active Work (Dependency Order)

### A-001 - Remote CI and release acceptance (P0)

- [ ] Re-run the workflow after the local POSIX SQLite normalization and CI tmpfs-size fixes; verify root `Tests` and both Docker acceptance jobs pass from a clean checkout.
- [ ] Verify the CI Docker containers have enough writable `/tmp` space for the packaged offline FastEmbed cache and retain useful failure logs.
- [ ] Verify the newly added CI stop/restart/resume step crosses a real container boundary for both engines and passes on GitHub-hosted Ubuntu runners.
- [ ] Verify remote wheel contents, image tags/version metadata, exact lock inputs, image-size gate, and container acceptance.
- Acceptance: the workflow is green and reproducible on GitHub-hosted runners.

### A-002 - CTK compatibility gate (P1)

- [ ] Verify the selected CTK subset in a green remote workflow and reconcile capability wording with actual coverage.
- [ ] Expand coverage only for scenarios supported by the implemented Portable Profile and declared capabilities.
- [ ] Preserve pinned CTK provenance and upload scenario/test output as a CI artifact for compatibility review.
- Acceptance: applicable CTK results are reproducible in CI without claiming unsupported scenarios.

### A-003 - Configured external persistence (P2)

- [ ] Add a locked PostgreSQL-capable datasource abstraction for common invocation metadata, memory, and knowledge metadata stores; keep SQLite as the reference backend.
- [ ] Add explicit, separate table/schema namespaces for invocation metadata, memory, knowledge metadata, and each engine's native durable state.
- [ ] Integrate engine-native PostgreSQL durability where the pinned ADK and LangGraph APIs support it; otherwise document and test the closest architecture-compatible adapter without sharing checkpoint representations.
- [ ] Add deterministic unit/contract coverage and Docker PostgreSQL acceptance for persistence across service restart and for both engine images.
- [ ] Keep unsupported datasource URLs failing explicitly until their backend and isolation tests exist.
- Acceptance: configured datasource behavior is durable, explicit, isolated, and architecture-compatible.

## Deferred Future Candidates

`listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
