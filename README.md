# Open Workflow Agent

Open Workflow Agent is a configuration-driven, model-agnostic runtime for executing Open Workflow 1.0.3 definitions through interchangeable ADK and LangGraph engines. The same public configuration is intended to work with either engine image.

The framework-neutral core loads and validates the official schema, applies the OWA Portable Profile capability gate, builds an internal immutable execution plan, and provides common catalogs, `jq` data semantics, knowledge, memory, protocols, persistence metadata, observability, and API errors. Engine packages own native agent/tool bindings and checkpointing. Every invocation runs through a workflow; without one, the runtime generates a workflow calling `agent:1.0.0@default`.

## Quick Start

Install the locked development environment and run the local quality gates:

```text
uv sync --locked
uv run pytest -q
```

For a deterministic local server, use a configuration such as:

```yaml
model:
  provider: fake
  name: fake/default
```

Then set `OWA_CONFIG_FILE` to that file and run an engine entry point. From `engines/adk`:

```text
uv run --locked --extra native python -m open_workflow_agent_adk.server
```

From `engines/langgraph`, use `--extra sqlite` and `open_workflow_agent_langgraph.server`. The API is available on port 8080 by default:

```text
GET  /health/live
GET  /health/ready
GET  /v1/capabilities
POST /v1/invoke
POST /v1/invocations/{id}/resume
POST /v1/admin/knowledge/reload
```

Mount deployment configuration at `/config`, documents at `/knowledge`, and writable runtime state at `/data`. The Dockerfiles build separate ADK and LangGraph images and prefetch the pinned local CPU embedding model; image acceptance is still pending a Docker-capable runner.

## Development

Use typed Python, strict configuration, deterministic `FakeModel` tests, and independent engine locks. Run formatting, linting, type checking, root/contract tests, engine suites, and package builds before a change is considered ready. No automated test requires paid model/API access.

## Project Status and References

The core and engine feature milestones are implemented. Active work is acceptance and release hardening: CI/container runners, real image E2E, image-level knowledge and embedding checks, container resume, CTK compatibility, configured datasource persistence, and agent memory exposure.

- [Project Definition.md](Project%20Definition.md) - authoritative architecture and requirements
- [PROJECT.md](PROJECT.md) - verified working context and commands
- [TODO.md](TODO.md) - dependency-ordered active implementation plan
- [AGENTS.md](AGENTS.md) - contributor and autonomous-agent rules
