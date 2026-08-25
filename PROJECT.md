# Project Context

## Source of Truth

`Project Definition.md` is the complete authoritative specification. This file records verified implementation context only; resolve conflicts in favor of the specification.

## Architecture Summary

Open Workflow Agent is a configuration-driven, model-agnostic runtime:

`load -> official schema validate -> Portable Profile capability gate -> normalize -> immutable plan -> engine compile -> invoke`

The framework-neutral core owns public contracts, the byte-identical official Open Workflow 1.0.3 schema, data semantics, `jq`, catalogs, protocols, knowledge, memory, invocation metadata, and common errors. ADK and LangGraph are separate engine packages with exact locks and native durability. The public API exposes common invocation/session identifiers; engine execution references remain internal metadata.

Every invocation executes through a workflow. The default workflow calls `agent:1.0.0@default`; `agent` and `llm` remain distinct catalog functions. Portability is demonstrated by shared fixtures and contract tests.

## Repository Structure

- `core/`: framework-neutral package and standalone build metadata.
- `engines/adk/`, `engines/langgraph/`: engine adapters, native integrations, and independent `uv.lock` files.
- `runtime-catalog/`, `resources/`: built-in functions and the official schema.
- `docker/`: independent ADK and LangGraph images.
- `tests/core/`, `tests/contract/`, `tests/adk/`, `tests/langgraph/`, `tests/e2e/`: layered tests.
- `AGENTS.md`, `TODO.md`: operational rules and executable backlog.

## Development Conventions

Use typed Python, strict Pydantic configuration, four-space indentation, `snake_case` modules/functions, `PascalCase` classes, immutable plans, and stable task references such as `/do/2/classify`. Keep framework imports inside engine packages. Use deterministic `FakeModel` behavior in tests; never require paid providers. Keep knowledge, memory, sessions, and engine checkpoints separate.

## Verified Status

Root quality currently passes: 65 tests, 4 skips; contract tests pass 32 cases. ADK passes 36 tests and LangGraph passes 36 tests in their locked native environments. Root and standalone core wheels include the official schema. Exact dependency locks, native tool bindings, lifecycle events, API hardening, protocol security/idempotency, official schema validation, and shared Portable Profile parity are implemented. Process/service close-and-reopen interrupted resume tests pass for both native engines.

Remaining acceptance is deliberately narrow: Docker daemon availability is required for B-002/B-003/B-006 image checks and B-004 container stop/restart proof. The official Gherkin CTK adapter/harness is not integrated (B-011). Public wording must remain `Open Workflow 1.0.3 based runtime` / `supports OWA Portable Profile v1`, not full conformance. Protocols are secure HTTP-backed adapters rather than full protocol SDKs; SQLite is the reference persistence backend and external datasource URLs are not wired.

## Build and Test Commands

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

Run engine suites independently:

```text
cd engines/adk
uv run --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract -q
cd ../langgraph
uv run --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract -q
```

Container commands are `docker build -f docker/Dockerfile.adk .` and the analogous LangGraph command. They cannot currently run because the Docker Desktop Linux daemon is unavailable.
