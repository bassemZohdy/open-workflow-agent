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

- [ ] Run the GitHub Actions workflow on push and pull request and verify every Ubuntu job passes from a clean checkout.
- [ ] Verify remote wheel contents, image tags/version metadata, exact lock inputs, image-size gate, and container acceptance.
- Acceptance: the workflow is green and reproducible on GitHub-hosted runners.

### A-002 - CTK compatibility gate (P1)

- [ ] Verify the selected CTK subset in a green remote workflow and reconcile capability wording with actual coverage.
- [ ] Expand coverage only for scenarios supported by the implemented Portable Profile and declared capabilities.
- Acceptance: applicable CTK results are reproducible in CI without claiming unsupported scenarios.

### A-003 - Configured external persistence (P2)

- [ ] Implement a locked external datasource backend usable by both native engines for invocation metadata, memory, knowledge metadata, and engine-owned persistence with isolated namespaces.
- [ ] Keep SQLite as the reference backend and fail unsupported datasource URLs explicitly until the external backend exists.
- Acceptance: configured datasource behavior is durable, explicit, isolated, and architecture-compatible.

## Deferred Future Candidates

`listen`, `emit`, CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines.

## Explicit Non-Goals

Do not build a visual designer, custom workflow DSL, BPMN engine, distributed scheduler, custom LLM/MCP/A2A protocols, enterprise vector database, multi-tenant UI, arbitrary Python plugins, or arbitrary shell execution in the initial project.
