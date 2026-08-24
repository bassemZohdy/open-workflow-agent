# Project Context

## Source of Truth

`Project Definition.md` is the complete and authoritative specification. This file is a working summary; consult the specification for details and resolve conflicts in its favor.

## Architecture Summary

Open Workflow Agent is a configuration-driven, model-agnostic platform. Configuration, knowledge, memory, tools, and an Open Workflow 1.0.3 definition enter a common pipeline:

`load -> schema validate -> capability validate -> normalize -> canonical execution plan -> engine compile -> invoke`

The plan is internal, typed, immutable, and derived. The framework-neutral `core` owns public contracts, Open Workflow data semantics, `jq`, catalog resolution, common protocol services, knowledge, memory, invocation metadata, and error translation. ADK and LangGraph are interchangeable runtime engines with separate dependency environments and Docker images. Durability stays native to each engine.

Every invocation is a workflow invocation. When no workflow is configured, generate the default one-task workflow calling `agent:1.0.0@default`. `agent:1.0.0@default` and `llm:1.0.0@default` are distinct catalog functions. Portability is proved by shared contract fixtures, not by adapter claims.

## Repository Structure

- `core/`: shared package and `src/open_workflow_agent/` modules.
- `engines/adk/`, `engines/langgraph/`: engine packages, source, locks, and image-specific behavior.
- `runtime-catalog/`, `resources/`: built-in functions and Open Workflow resources.
- `docker/`: separate ADK and LangGraph Dockerfiles.
- `tests/contract/`, `tests/core/`, `tests/adk/`, `tests/langgraph/`, `tests/e2e/`: layered tests.
- `docs/`, `examples/`: supporting documentation and fixtures.

## Development Conventions

Use typed Python, strict Pydantic configuration models, four-space indentation, `snake_case` modules/functions, `PascalCase` classes, and immutable plan models where practical. Keep framework-specific code in its engine package. Preserve stable Open Workflow task references such as `/do/2/classify`. Use deterministic `FakeModel` behavior; CI must not require paid providers. Keep knowledge, memory, session, and checkpointing as separate concepts.

## Current Implementation Status

The repository is initialized with directory scaffolding and context files only. No package manifests, runtime code, workflow schema resources, Dockerfiles, or test suite are implemented. Milestone 0 is not started.

## Build and Test Commands

There are currently no runnable build or test commands. Once package manifests exist, run each package independently:

```text
uv sync
pytest
pytest tests/contract
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```

Run commands from the relevant package/repository context and never install dependencies during container startup.
