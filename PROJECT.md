# Project Context

## Source of Truth and Current Phase

`Project Definition.md` is authoritative; `AGENTS.md` contains mandatory contributor rules and `TODO.md` is the active backlog. Core implementation, local acceptance, remote CI/release verification, the applicable CTK gate, configured PostgreSQL persistence acceptance, B-001, B-002, and the bounded B-003 eventing/CloudEvents/scheduling/sub-workflow/HITL slices are complete. The current phase remains B-003, with secure external catalog resolution as the next implementation boundary.

## Architecture

The runtime is `load -> official schema validation -> Portable Profile gate -> normalize -> immutable internal plan -> engine execution`. Core is framework-neutral. `engines/adk` and `engines/langgraph` are separate packages with exact locks, native agent/tool adapters, and engine-owned state. Every request executes a workflow, with a generated default workflow when none is supplied. Mounted knowledge uses the common `EmbeddingProvider`; production images package FastEmbed 0.8.0/ONNX and the 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model identity. Deterministic hash embeddings remain injectable for tests.

## Repository Structure and Conventions

- `core/`: common configuration, schema, workflow semantics, catalogs, services, API, persistence metadata, approval/schedule state, and errors.
- `engines/adk/`, `engines/langgraph/`: independent adapters, native persistence, locks, and package metadata.
- `resources/`, `runtime-catalog/`: official resources and built-in catalog.
- `docker/`: independent multi-stage runtime images; `.github/workflows/ci.yml`: Ubuntu quality/container gates.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/ctk`, `tests/e2e`: layered deterministic coverage.
- `core/src/open_workflow_agent/protocols.py`: bounded common HTTP/MCP/A2A/OpenAPI clients used by workflow calls and configured agent tools.
- `core/src/open_workflow_agent/approvals.py`: bounded durable approval state and replay layered on standard event/listen semantics.

Use strict typed Python, four-space indentation, exact dependency locks, shared contract fixtures, and `FakeModel`; tests must not require paid APIs. Do not install packages at container startup.

## Verified Status

Root format/lint/mypy/tests/contracts, ADK/LangGraph native suites, selected CTK, Docker image/health/knowledge/restart-resume gates, and PostgreSQL persistence acceptance remain green. The durable HITL implementation was merged through PR #2 after green branch CI run `32849496990`; the subsequent PostgreSQL common-store acceptance also verifies the isolated `owa_approvals` namespace and persisted decisions. The merge to `main` triggers the same full Ubuntu workflow.

SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented behind locked `postgres` extras with isolated namespaces. Runtime images remain below the 2 GiB quality gate and use packaged FastEmbed/ONNX knowledge embeddings rather than Torch/CUDA.

## Completed B-003 Behavior

- Generic `emit`/`listen(one)` event delivery is process-local and non-durable.
- Lifecycle events are available as bounded CloudEvents 1.0 JSON snapshots.
- `schedule.after` and `schedule.every` persist scheduler state with restart reclaim and at-least-once semantics.
- Local sub-workflows use the standard `run` task against deployment-configured `workflow.catalog` definitions.
- Durable HITL approvals use standard event composition rather than a proprietary task: approval request/decision state is persisted separately, operator decisions are bearer-authorized and idempotent, inbox reads are protected, and terminal decisions replay through the normal `listen` path after restart. ADK and LangGraph share the same observable contract. The bearer/operator-header guard is deliberately a bounded deployment authorization boundary, not a replacement for an enterprise identity provider.

## Current Next Step

Implement secure external catalog resolution without weakening the trust boundary. Open Workflow `use.catalogs` remains rejected until a deployment-controlled resolver has explicit allowlists/trust configuration, TLS verification, timeout/redirect/response-size controls, integrity or version pinning, caching/revalidation, fail-closed errors, capabilities reporting, and deterministic cross-engine contracts. After that, evaluate optional A2A exposure/streaming and only then additional engines.

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
cp .env.example .env
docker compose --profile adk up --build
# or: docker compose --profile langgraph up --build
docker compose down
```
