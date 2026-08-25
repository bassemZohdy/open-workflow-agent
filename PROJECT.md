# Project Context

## Source of Truth and Phase

`Project Definition.md` is authoritative; `AGENTS.md` is mandatory guidance and `TODO.md` is the active backlog. Core feature milestones are implemented. The current phase is acceptance/release hardening: the next action is to run and inspect the new GitHub Actions workflow, then integrate the applicable Open Workflow CTK scenarios.

## Architecture

The runtime is `load -> official schema validation -> Portable Profile gate -> normalize -> immutable internal plan -> engine execution`. Core is framework-neutral. `engines/adk` and `engines/langgraph` have separate exact locks, native agent/tool adapters, and engine-owned state. Every request executes a workflow, with a generated default workflow when none is supplied. Mounted knowledge uses the common `EmbeddingProvider`; the packaged default is FastEmbed 0.8.0/ONNX using the 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model identity. Deterministic hash embeddings remain injectable for tests.

## Repository Structure and Conventions

- `core/`: common configuration, schema/data semantics, catalogs, services, API, and errors.
- `engines/adk/`, `engines/langgraph/`: independent adapters, native persistence, locks, and package metadata.
- `resources/`, `runtime-catalog/`: official Open Workflow resources and built-in catalog.
- `docker/`: independent multi-stage runtime images; `.github/workflows/ci.yml`: Ubuntu quality/container gates.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/e2e`: layered deterministic coverage.

Use strict typed Python, four-space indentation, exact dependency locks, shared contract fixtures, and `FakeModel`; never install packages at container startup or require paid APIs in tests.

## Verified Status

Local gates: root `65 passed, 4 skipped`; contract `32 passed`; ADK `36 passed` with one pinned-framework deprecation warning; LangGraph `36 passed`; format, lint, mypy, lock checks, root/core builds, and schema wheel checks pass.

Original images were ADK `3,061,701,964` bytes and LangGraph `3,048,836,739` bytes. Final optimized images are ADK `202,387,127` bytes and LangGraph `197,563,217` bytes. The bloat was CUDA/Torch (`nvidia` about 2.7 GB, Torch about 1.1 GB) retained in the Sentence-Transformers dependency graph. Multi-stage builds retain only a 226–245 MB runtime environment and an 87 MB local model; `/root`, `/tmp`, and build caches are absent from the final images.

Both images pass local health, capabilities, FakeModel, mounted config/knowledge, reload/watch/deletion/restart, arbitrary-UID, read-only-root, and offline search checks. Fresh container stop -> restart -> resume -> complete tests pass for both engines with persisted `adk-sessions.sqlite3` / `langgraph-checkpoints.sqlite3`, stable workflow fingerprints, and repeated side effects carrying the same idempotency key. GitHub Actions is added but has no remote run result until a push/PR executes it.

## Commands

```text
uv sync --locked
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q
uv run pytest tests/contract -q
uv build
uv build --directory core
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract -q
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract -q
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```

Remaining limitations are the applicable CTK gate, configured external `persistence.datasource`, and agent-native `MemoryService` exposure. Protocol adapters are secure HTTP-backed implementations rather than full SDKs; SQLite remains the reference backend. Deferred streaming, HITL, scheduling, CloudEvents, additional engines, and other future candidates remain out of scope.
