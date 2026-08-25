# Project Context

## Source of Truth and Current Phase

`Project Definition.md` is authoritative; `AGENTS.md` contains mandatory contributor rules and `TODO.md` is the active backlog. Core implementation and local acceptance are complete. The current phase is remote CI/release verification, followed by the applicable CTK gate and then external persistence.

## Architecture

The runtime is `load -> official schema validation -> Portable Profile gate -> normalize -> immutable internal plan -> engine execution`. Core is framework-neutral. `engines/adk` and `engines/langgraph` are separate packages with exact locks, native agent/tool adapters, and engine-owned state. Every request executes a workflow, with a generated default workflow when none is supplied. Mounted knowledge uses the common `EmbeddingProvider`; production images package FastEmbed 0.8.0/ONNX and the 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model identity. Deterministic hash embeddings remain injectable for tests.

## Repository Structure and Conventions

- `core/`: common configuration, schema, workflow semantics, catalogs, services, API, persistence metadata, and errors.
- `engines/adk/`, `engines/langgraph/`: independent adapters, native persistence, locks, and package metadata.
- `resources/`, `runtime-catalog/`: official resources and built-in catalog.
- `docker/`: independent multi-stage runtime images; `.github/workflows/ci.yml`: Ubuntu quality/container gates.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/ctk`, `tests/e2e`: layered deterministic coverage.

Use strict typed Python, four-space indentation, exact dependency locks, shared contract fixtures, and `FakeModel`; tests must not require paid APIs. Do not install packages at container startup.

## Verified Status

Local root, core, contract, ADK, LangGraph, selected CTK, format, lint, mypy, lock, build, and schema-wheel checks pass. Local images are below the 2 GiB gate: ADK is approximately 202 MB and LangGraph approximately 198 MB. Both pass local health, capabilities, FakeModel, mounted-knowledge, read-only-root, arbitrary-UID, offline-search, reload, restart, and stop/restart/resume acceptance. The original images were approximately 3.06 GB and 3.05 GB; CUDA/Torch and the Sentence-Transformers dependency graph were removed in favor of FastEmbed/ONNX and a packaged local model.

The GitHub workflow is present. Remote run `32805216540` for commit `1339118` completed with a wheel-content assertion failure and both Docker acceptance jobs failing to start because the mounted `/data` directory was not writable by the arbitrary UID. The ADK and LangGraph engine jobs passed. The wheel check now validates the packaged Open Workflow 1.0.3 YAML resource, CI gives mounted state writable permissions, verifies image/release metadata, retains logs, and includes real stop/restart/resume and PostgreSQL jobs; these changes remain pending remote verification. SQLite remains the reference datasource, and PostgreSQL common stores plus ADK/LangGraph native PostgreSQL adapters are implemented behind the locked `postgres` extras. The applicable CTK subset is locally integrated, while the remote CTK gate and full persistence acceptance remain open.

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
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```
