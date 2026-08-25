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
- `docs/`, `examples/`: supporting documentation and fixtures.

## Development Conventions

Use typed Python, strict Pydantic configuration models, four-space indentation, `snake_case` modules/functions, `PascalCase` classes, and immutable plan models where practical. Keep framework-specific code in its engine package. Preserve stable Open Workflow task references such as `/do/2/classify`. Use deterministic `FakeModel` behavior; CI must not require paid providers. Keep knowledge, memory, session, and checkpointing as separate concepts.

## Current Implementation Status

Milestone 0 core contracts and the current deterministic Portable Profile are implemented and verified. The repository now has strict configuration, workflow loading/validation/capability checks, default workflow generation, immutable plans and fingerprints, a catalog, engine SPI, common errors, invocation persistence, memory, knowledge indexing, protocol clients, and a FastAPI surface. ADK and LangGraph adapter boundaries and deterministic contract tests are present.

The full upstream Open Workflow 1.0.3 schema is not yet vendored; the current validator is a local Portable Profile structural gate carrying the official schema identifier. ADK 2.7.1 native dynamic execution and SQLite session restart/resume pass in the engine environment. LangGraph invokes the native Functional API with an optional SQLite checkpointer and retains the deterministic reference fallback. Dockerfiles exist but cannot be built until a Docker daemon is available in the environment. Protocol clients currently provide secure HTTP-backed adapters rather than full protocol SDK implementations. The reference persistence backend is SQLite; configured datasource URLs are not yet wired to external databases.

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

The Docker commands remain pending until the image definitions are added. Run engine dependency environments independently and never install dependencies during container startup.
