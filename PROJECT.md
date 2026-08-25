# Project Context

## Source of Truth

`Project Definition.md` is the complete and authoritative specification. This file is a working summary; consult the specification for details and resolve conflicts in its favor.

## Architecture Summary

Open Workflow Agent is a configuration-driven, model-agnostic platform. Configuration, knowledge, memory, tools, and an Open Workflow 1.0.3 definition enter a common pipeline:

`load -> schema validate -> capability validate -> normalize -> canonical execution plan -> engine compile -> invoke`

The plan is internal, typed, immutable, and derived. The framework-neutral `core` owns public contracts, Open Workflow data semantics, `jq`, catalog resolution, common protocol services, knowledge, memory, invocation metadata, and error translation. ADK and LangGraph are interchangeable runtime engines with separate dependency environments and Docker images. Durability stays native to each engine.

Every invocation is a workflow invocation. When no workflow is configured, generate the default one-task workflow calling `agent:1.0.0@default`. `agent:1.0.0@default` and `llm:1.0.0@default` are distinct catalog functions. Portability is proved by shared contract fixtures, not by adapter claims.

## Repository Structure

- `core/`: shared package, `pyproject.toml`, and `src/open_workflow_agent/` modules.
- `engines/adk/`, `engines/langgraph/`: engine packages, source, independent `uv.lock` files, and image-specific behavior.
- `runtime-catalog/`, `resources/`: built-in functions and Open Workflow resources.
- `docker/`: separate ADK and LangGraph Dockerfiles.
- `tests/contract/`, `tests/core/`, `tests/adk/`, `tests/langgraph/`, `tests/e2e/`: layered tests.
- `docs/` and `examples/`: reserved for future supporting material.

## Development Conventions

Use typed Python, strict Pydantic configuration models, four-space indentation, `snake_case` modules/functions, `PascalCase` classes, and immutable plan models where practical. Keep framework-specific code in its engine package. Preserve stable Open Workflow task references such as `/do/2/classify`. Use deterministic `FakeModel` behavior; CI must not require paid providers. Keep knowledge, memory, session, and checkpointing as separate concepts.

## Current Implementation Status

The implementation contains the M0–M7 core scope: strict configuration, workflow loading/default generation, a typed immutable plan, catalog functions, Portable Profile execution, ADK and LangGraph adapter/native paths, knowledge and memory services, invocation persistence/resume APIs, protocol clients, configured tool definitions, and the HTTP surface. The latest verified quality run was 30 root tests passed/2 skipped, 12 contract tests passed, and 14 tests passed in each engine environment.

The milestones are not yet complete against the full specification. The current schema validator is a local Portable Profile structural gate; the exact upstream Open Workflow 1.0.3 schema is not vendored. Native tests restart services after a completed invocation rather than proving interrupted container resume. The Dockerfiles have not been validated and currently need packaging fixes so each image installs its own engine/native dependencies independently. Knowledge uses a deterministic hash embedding provider instead of a pinned local embedding model. Configured tools are registered centrally but are not yet wired through real engine-native tool-call loops. Common lifecycle events/task-level observability, request-size/readiness enforcement, protocol authentication/idempotency, and full Portable Profile fixture parity remain backlog work.

ADK 2.7.1 dynamic execution and LangGraph Functional API paths are available in their engine environments, with a deterministic reference fallback for core-only environments. Protocol clients are secure HTTP-backed adapters rather than full protocol SDK implementations. SQLite is the reference persistence backend; configured external datasource URLs are not yet wired to external databases. Public wording must remain “Open Workflow 1.0.3 based runtime” / “supports OWA Portable Profile v1”, not full Open Workflow conformance.

## Reviewed Backlog Priorities

Work is tracked in `TODO.md` by dependency and priority:

1. `B-001` — vendor and enforce the exact upstream schema.
2. `B-002` — make the ADK and LangGraph images independently buildable and runnable.
3. `B-003`/`B-004` — complete image-based knowledge and interrupted-resume acceptance.
4. `B-005`/`B-006`/`B-007` — finish contract parity, the pinned embedding model, and native agent tools.
5. `B-008`/`B-009`/`B-010` — add observability and runtime/security hardening.
6. `B-011` — add the Open Workflow CTK gate after schema and fixture parity.

## Build and Test Commands

Run the repository quality gates with:

```text
uv sync
uv run ruff format core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q
uv run pytest tests/contract -q
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```

The package build passes. Docker image commands are currently blocked by the unavailable daemon and remain a backlog acceptance gate. Run engine dependency environments independently and never install dependencies during container startup. Engine-specific gates use `uv run --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract -q` from `engines/adk`, with the analogous `tests/langgraph` command from `engines/langgraph`.
