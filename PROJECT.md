# Project Context

## Source of Truth and Current Phase

`Project Definition.md` is authoritative; `AGENTS.md` contains mandatory contributor rules and `TODO.md` is the active backlog. Core implementation, local acceptance, remote CI/release verification, the applicable CTK gate, configured PostgreSQL persistence acceptance, B-001, B-002, and the bounded B-003 eventing/CloudEvents/scheduling/sub-workflow/HITL slices are complete. The current phase is B-004, secure external catalog resolution. The first implementation slice is present in core behind explicit deployment trust; deterministic security/API coverage, connection-level DNS-rebinding resistance, and resolve-before-plan ordering are now verified locally, while completion remains gated on final container/CI acceptance.

## Architecture

The runtime is `load -> official schema validation -> Portable Profile gate -> normalize -> immutable internal plan -> engine execution`. Core is framework-neutral. `engines/adk` and `engines/langgraph` are separate packages with exact locks, native agent/tool adapters, and engine-owned state. Every request executes a workflow, with a generated default workflow when none is supplied.

Standard runtime images bundle the common LiteLLM model adapter/runtime for configured external model providers and FastEmbed 0.8.0/ONNX with the 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model identity for local knowledge embeddings. Deterministic `FakeModel`/hash embeddings remain available for tests so CI never requires paid model access.

## Repository Structure and Conventions

- `core/`: common configuration, schema, workflow semantics, catalogs, services, API, persistence metadata, approval/schedule state, and errors.
- `engines/adk/`, `engines/langgraph/`: independent adapters, native persistence, locks, and package metadata.
- `resources/`, `runtime-catalog/`: official resources and built-in catalog.
- `docker/`: independent multi-stage runtime images; `.github/workflows/ci.yml`: Ubuntu quality/container gates; `.github/workflows/release.yml`: verified GHCR release publication.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/ctk`, `tests/e2e`: layered deterministic coverage.
- `core/src/open_workflow_agent/protocols.py`: bounded common HTTP/MCP/A2A/OpenAPI clients used by workflow calls and configured agent tools.
- `core/src/open_workflow_agent/approvals.py`: bounded durable approval state and replay layered on standard event/listen semantics.

Use strict typed Python, four-space indentation, exact dependency locks, shared contract fixtures, and `FakeModel`; tests must not require paid APIs. Do not install packages at container startup.

## Verified Status

Root format/lint/mypy/tests/contracts, ADK/LangGraph native suites, selected CTK, Docker image/health/knowledge/restart-resume gates, and PostgreSQL persistence acceptance remain green. CI run `32915495802` verifies the standard images after adding locked LiteLLM 1.80.5 to both independent engine dependency graphs. GitHub Actions run `32930787715` verifies the current catalog-resolution change and passed every root, engine, CTK, Docker, and PostgreSQL job for commit `75be75603620a4155fd49e8e4f89d721bb437dec`.

The Docker build performs an explicit LiteLLM import check. The same CI run verifies image metadata, the 2 GiB quality gate, arbitrary-UID/read-only-root execution, health/readiness, deterministic invocation, mounted knowledge, genuine container stop/restart/resume, and PostgreSQL persistence for both engines.

The latest local external-catalog hardening adds one-shot DNS resolution with public-address validation and a pinned HTTP transport that connects only to the approved addresses while preserving hostname-based TLS verification. The full local suite passes with `169 passed, 6 skipped`; Ruff format/lint and mypy also pass. The latest remote push run for the then-current main commit was cancelled after the in-progress run stop, so the published change still requires a fresh CI run.

Verified standard image sizes with LiteLLM, local FastEmbed/ONNX knowledge embeddings, PostgreSQL support, and native engine dependencies are:

```text
ADK        575,408,181 bytes (~575 MB decimal)
LangGraph  514,672,129 bytes (~515 MB decimal)
```

This is intentionally larger than the earlier fake-only optimized images but remains far below the 2 GiB gate and avoids the multi-gigabyte Torch/CUDA dependency path.

SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented behind locked `postgres` extras with isolated namespaces. Real model providers are selected through configuration and provider-specific secrets; no source checkout or image rebuild is required for the standard LiteLLM path.

## Completed B-003 Behavior

- Generic `emit`/`listen(one)` event delivery is process-local and non-durable.
- Lifecycle events are available as bounded CloudEvents 1.0 JSON snapshots.
- `schedule.after` and `schedule.every` persist scheduler state with restart reclaim and at-least-once semantics.
- Local sub-workflows use the standard `run` task against deployment-configured `workflow.catalog` definitions.
- Durable HITL approvals use standard event composition rather than a proprietary task: approval request/decision state is persisted separately, operator decisions are bearer-authorized and idempotent, inbox reads are protected, and terminal decisions replay through the normal `listen` path after restart. ADK and LangGraph share the same observable contract. The bearer/operator-header guard is deliberately a bounded deployment authorization boundary, not a replacement for an enterprise identity provider.

## Release Images

Verified stable tags are published by `.github/workflows/release.yml` only after CI succeeds for the tagged commit:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<version>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<version>
```

The release pipeline adds exact-version, minor-series, `latest`, and immutable source-SHA tags plus SBOM/provenance metadata and GitHub build provenance attestations.

## Current Next Step

Finish B-004 secure external catalog resolution without weakening the trust boundary. The current slice adds deployment-controlled alias/host/endpoint policy, HTTPS/TLS enforcement, bounded streaming fetches, no redirects, environment-only authentication, semantic-version references, optional/required SHA-256 pins, isolated cache/revalidation, sanitized capability state, equivalent ADK/LangGraph contract coverage, resolve-before-plan ordering across startup, child workflows, and schedules, and connection-level DNS-rebinding resistance. Remaining acceptance work is container validation with an explicitly configured external catalog and a fresh CI run; the general root, engine, CTK, Docker, and PostgreSQL gates remain green from the previous verified commit. After B-004, evaluate optional A2A exposure/streaming and only then additional engines.

Full MCP, A2A, OpenAPI, external-catalog, streaming, or Open Workflow ecosystem conformance remains unclaimed beyond the tested Portable Profile/capabilities.

## Key Commands

```text
uv sync --locked
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q
uv build
uv build --directory core
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
uv run --locked pytest tests/core/test_protocols.py tests/contract/test_contract.py tests/contract/test_tools.py -q
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```
