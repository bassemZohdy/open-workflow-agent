# Project Context

## Source of Truth

`Project Definition.md` is authoritative. `AGENTS.md` contains mandatory development rules. `TODO.md` is the active acceptance plan; this file is verified working context, not a replacement specification.

## Current Phase

The implementation is past the M0-M7 feature milestones and is now in acceptance and release-hardening. Do not begin deferred features. The next dependency-ordered action is A-001 in `TODO.md`: establish CI and a Docker-capable runner so image acceptance can be reproduced even when local Docker is unavailable.

## Architecture and Capabilities

The runtime is:

`load -> official schema validation -> Portable Profile gate -> normalize -> immutable plan -> engine execution`

Core is framework-neutral and owns configuration, the byte-identical official Open Workflow 1.0.3 schema, Open Workflow data semantics, `jq`, catalogs, protocols, knowledge, memory, invocation metadata, observability events, and common errors. ADK and LangGraph are separate packages with exact dependency locks, native agent/tool adapters, and engine-owned durability. Every invocation uses a workflow; missing workflow generates the default `agent:1.0.0@default` workflow. `agent` and `llm` remain distinct catalog functions.

Implemented public behavior includes `/v1/invoke`, resume, capabilities, liveness/readiness, knowledge reload, request-size/error handling, Portable Profile tasks and calls, MCP/A2A/OpenAPI HTTP-backed calls, configured native tools, local knowledge indexing, protocol security/idempotency, lifecycle events, and deterministic FakeModel coverage.

## Repository Structure

- `core/`: framework-neutral runtime package and standalone build metadata.
- `engines/adk/`, `engines/langgraph/`: engine adapters, native integrations, and independent locks.
- `resources/`, `runtime-catalog/`: official schema and built-in catalog functions.
- `docker/`: separate engine images.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/e2e`: layered tests.

## Verified Status

Fresh local gates pass: root `65 passed, 4 skipped`; contract `32 passed`; ADK `36 passed` with one pinned-framework deprecation warning; LangGraph `36 passed`. Locked sync and lock checks pass for root, ADK, and LangGraph. Ruff format/check, mypy, root build, standalone core build, and schema wheel inclusion pass. The four root skips are optional native-engine tests; engine-specific locked suites provide the native verification.

Not yet verified: Docker build/runtime smoke tests, image-level knowledge and local-model acceptance, container stop/restart/resume, CI reproducibility, and the official CTK gate. `persistence.datasource` is configured but not wired to a shared external backend, and `MemoryService` is not yet exposed as an agent-native tool. Protocols are secure HTTP-backed adapters rather than full protocol SDKs; SQLite remains the reference persistence backend.

## Development Commands

```text
uv sync --locked
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q
uv run pytest tests/contract -q
uv build
uv build --directory core
```

Engine suites run from their package directories with their locked `uv.lock` files:

```text
uv run --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract -q
uv run --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract -q
```

Docker commands are `docker build -f docker/Dockerfile.adk .` and the analogous LangGraph command. Current local Docker fails because the Docker Desktop Linux daemon is unavailable.
