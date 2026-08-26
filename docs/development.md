# Developer Guide

This guide is for contributors extending Open Workflow Agent itself.

Before changing architecture or public contracts, read:

1. `Project Definition.md` — authoritative architecture/product contract.
2. `PROJECT.md` — verified implementation status.
3. `TODO.md` — active ordered backlog.
4. `AGENTS.md` — mandatory repository rules.

## Architecture boundary

The dependency direction is:

```text
API
 |
 v
Core
 |
 v
Engine SPI
 ^
 |
ADK / LangGraph
```

Core must not import ADK or LangGraph.

Core owns:

- configuration;
- Open Workflow loading and validation;
- portable capability checks;
- normalization/internal execution plan;
- jq/data semantics;
- catalog resolution;
- knowledge and memory abstractions;
- protocol services;
- invocation metadata;
- common errors and lifecycle events.

Each engine owns:

- framework-native workflow execution;
- framework-native agent/tool integration;
- checkpoint/resume integration;
- engine-specific persistence;
- framework exception translation at the boundary.

## Repository layout

```text
core/                     framework-neutral runtime
engines/adk/              ADK adapter and dependency lock
engines/langgraph/        LangGraph adapter and dependency lock
runtime-catalog/          built-in runtime functions
resources/                Open Workflow resources/schema assets
tests/core/               common unit/integration tests
tests/contract/           cross-engine portable fixtures
tests/adk/                ADK-specific tests
tests/langgraph/          LangGraph-specific tests
tests/ctk/                selected Open Workflow CTK coverage
tests/e2e/                container/end-to-end coverage
docker/                   engine Dockerfiles and entrypoint
docs/                     user/operator/developer documentation
```

## Local setup

```bash
uv sync --locked
```

Run the common test suite:

```bash
uv run pytest -q
```

Formatting/lint/type checks:

```bash
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy core/src
```

Build packages:

```bash
uv build
uv build --directory core
```

## Engine-specific tests

ADK:

```bash
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio \
  pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
```

LangGraph:

```bash
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio \
  pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
```

Do not combine engine dependency locks into one environment just for convenience.

## Contract tests define portability

A capability is portable only when the same fixture and input produce the expected equivalent behavior on both engines.

Shared fixtures live under:

```text
tests/contract/fixtures
```

When adding portable behavior:

1. add/update a shared fixture;
2. add common semantic coverage;
3. make ADK pass it;
4. make LangGraph pass it;
5. update `/v1/capabilities` only after the contract is actually supported.

Do not silently ignore unsupported Open Workflow features.

## Models in tests

Automated tests must not require paid APIs.

Use the common deterministic `FakeModel` for:

- simple responses;
- structured payloads;
- tool-call behavior;
- controlled failures/retries.

LiteLLM is optional runtime functionality, not a CI requirement.

## Adding a common feature

Examples: workflow semantics, protocol behavior, knowledge, memory, errors.

Keep implementation under `core/` when the behavior is engine-neutral. Expose only the minimum SPI required for engines to consume it.

Do not leak framework-native objects into public/runtime core models.

## Adding engine-specific behavior

Put framework integration under the corresponding engine package.

If an optional capability exists in only one engine, advertise it explicitly through capabilities rather than weakening the common contract or faking parity.

## Persistence rules

The common runtime owns public invocation identity, workflow fingerprint, status, and metadata.

ADK owns ADK durable/session state. LangGraph owns LangGraph checkpointer/store state.

Never make one engine read another engine's checkpoint representation.

## Workflow rules

Open Workflow 1.0.3 is the authoring DSL.

The internal canonical execution plan is:

- derived;
- typed;
- immutable;
- internal only.

Do not introduce a second external workflow language and do not fork the Open Workflow schema to add AI-specific calls. `agent:1.0.0@default` and `llm:1.0.0@default` are runtime catalog functions.

## Security expectations

Treat invocation/user input as untrusted and workflow definitions as trusted deployment artifacts.

Do not:

- log secrets;
- put credentials in ordinary workflow examples;
- enable arbitrary shell/script/container execution;
- bypass TLS/timeout/response-size protocol protections;
- dynamically install packages at runtime startup.

## Docker validation

Build both independently:

```bash
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```

Compose validation:

```bash
cp .env.example .env
docker compose --profile adk up --build
# or
docker compose --profile langgraph up --build
```

Remote CI additionally validates image metadata/size, container acceptance, PostgreSQL persistence, selected CTK coverage, and stop/restart/resume behavior.

## Documentation rule

When changing public configuration, API behavior, supported workflow semantics, deployment requirements, or capabilities, update the corresponding file under `docs/` and keep README quick-start examples valid.

Implementation status belongs in `PROJECT.md`; active work belongs in `TODO.md`; architecture decisions belong in `Project Definition.md`. Avoid turning README back into a status log.
