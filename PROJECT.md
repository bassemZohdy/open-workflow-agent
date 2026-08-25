# Project Context

## Source of Truth and Current Phase

`Project Definition.md` is authoritative; `AGENTS.md` contains mandatory contributor rules and `TODO.md` is the active backlog. Core implementation, local acceptance, remote CI/release verification, the applicable CTK gate, configured PostgreSQL persistence acceptance, B-001, and B-002 are complete. The current phase is B-003: Deferred workflow lifecycle features.

## Architecture

The runtime is `load -> official schema validation -> Portable Profile gate -> normalize -> immutable internal plan -> engine execution`. Core is framework-neutral. `engines/adk` and `engines/langgraph` are separate packages with exact locks, native agent/tool adapters, and engine-owned state. Every request executes a workflow, with a generated default workflow when none is supplied. Mounted knowledge uses the common `EmbeddingProvider`; production images package FastEmbed 0.8.0/ONNX and the 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model identity. Deterministic hash embeddings remain injectable for tests.

## Repository Structure and Conventions

- `core/`: common configuration, schema, workflow semantics, catalogs, services, API, persistence metadata, and errors.
- `engines/adk/`, `engines/langgraph/`: independent adapters, native persistence, locks, and package metadata.
- `resources/`, `runtime-catalog/`: official resources and built-in catalog.
- `docker/`: independent multi-stage runtime images; `.github/workflows/ci.yml`: Ubuntu quality/container gates.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/ctk`, `tests/e2e`: layered deterministic coverage.
- `core/src/open_workflow_agent/protocols.py`: bounded common HTTP/MCP/A2A/OpenAPI clients used by workflow calls and configured agent tools.

Use strict typed Python, four-space indentation, exact dependency locks, shared contract fixtures, and `FakeModel`; tests must not require paid APIs. Do not install packages at container startup.

## Verified Status

Local root, core, contract, eventing, ADK, LangGraph, selected CTK, format, lint, mypy, lock, diff, Compose configuration, and PostgreSQL persistence checks pass. The full remote workflow also passed root build and wheel checks, both engine suites, the selected CTK subset, both Docker acceptance jobs, and PostgreSQL persistence acceptance after B-002 lifecycle coverage was added.

The GitHub workflow is green on Ubuntu in remote run [`32831528433`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32831528433) for the bounded eventing milestone. It passed root tests/contracts, release metadata and all three lock checks, Compose profile validation, wheel resource validation, both image metadata and 2 GiB gates, both independent Docker health/invocation/knowledge/eventing gates, genuine stop/restart/resume across a container boundary for ADK and LangGraph, PostgreSQL common-store durability, and PostgreSQL acceptance for both engine images. The pinned CTK subset passed in both engine jobs and produced `ctk-adk-results` and `ctk-langgraph-results` artifacts containing test output, the repository commit, the pinned upstream CTK commit, and scenario hashes. SQLite remains the reference datasource; PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented behind locked `postgres` extras with isolated namespaces.

## Current Next Step

B-003 in `TODO.md` is the current phase. Its bounded `listen`/`emit` eventing slice and optional lifecycle CloudEvents 1.0 bounded snapshot boundary are complete; scheduling is next, followed by sub-workflows, HITL, external catalogs, optional A2A exposure and streaming, and additional engines. Full MCP, A2A, OpenAPI, or Open Workflow ecosystem conformance remains unclaimed.

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
