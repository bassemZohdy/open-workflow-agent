# Open Workflow Agent

Open Workflow Agent is a configuration-driven, model-agnostic runtime for Open Workflow 1.0.3. A framework-neutral core validates the official schema, applies the Portable Profile gate, builds an internal plan, and owns common data semantics, catalogs, knowledge, memory, protocols, persistence metadata, observability, and API errors. ADK and LangGraph are independently packaged engine adapters with native agent/tool bindings and durable state.

Every invocation is a workflow invocation. If no workflow is configured, the runtime generates the default workflow calling `agent:1.0.0@default`.

## Quick Start

Install the locked development environment and run the local gates:

```text
uv sync --locked
uv run pytest -q
```

For deterministic operation, configure:

```yaml
model:
  provider: fake
  name: fake/default
```

Run an engine from its package directory:

```text
uv run --locked --extra native python -m open_workflow_agent_adk.server
uv run --locked --extra sqlite python -m open_workflow_agent_langgraph.server
```

The API listens on port 8080 and exposes `/health/live`, `/health/ready`, `/v1/capabilities`, `/v1/invoke`, resume, and knowledge reload endpoints. Deployments mount configuration at `/config`, documents at `/knowledge`, and writable state at `/data`.

SQLite is the reference persistence backend. PostgreSQL is available through the locked `postgres` extra and uses separate namespaces for runtime metadata, memory, knowledge metadata, and each engine's native durable state:

```yaml
persistence:
  datasource: postgresql://user:password@host:5432/database
```

The ADK image uses ADK's database session service with `asyncpg`; the LangGraph image uses `langgraph-checkpoint-postgres`. Build/runtime images include these optional drivers without changing the public configuration.

## Containers and Status

Build independent images with `docker build -f docker/Dockerfile.adk .` or the LangGraph equivalent. Each image packages the pinned local FastEmbed/ONNX `all-MiniLM-L6-v2` model, runs as a non-root arbitrary UID, and has a 2 GiB CI size gate. GitHub Actions reproduces root, engine, Docker, CTK, and PostgreSQL gates on Ubuntu; green run [`32816720537`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32816720537) retained Docker and CTK provenance artifacts.

No automated test requires paid model/API access. The applicable remote CI, CTK, and PostgreSQL acceptance gates are green. See [Project Definition.md](Project%20Definition.md) for the specification, [PROJECT.md](PROJECT.md) for verified working context, [TODO.md](TODO.md) for active work, and [AGENTS.md](AGENTS.md) for contributor rules.

## Current support and roadmap

The common core currently provides bounded HTTP, MCP, A2A, and OpenAPI clients for explicit workflow calls and configured agent tools. Explicit workflow calls and agent tools remain separate execution paths. These paths enforce endpoint validation, optional host allowlists, TLS verification, bounded responses, redirect policy, authentication abstraction, operation identifiers, idempotency headers, and common error translation. `/v1/capabilities` reports the supported task, function, protocol, and retry/timeout policy surface. These are intentionally bounded adapters, not claims of full MCP, A2A, OpenAPI, or Open Workflow ecosystem conformance.

The next milestone is **Lifecycle and operational semantics**: common invocation/task events, cancellation and waiting-state behavior, restart/resume boundaries, and idempotent side effects. The ordered plan and acceptance criteria are in [TODO.md](TODO.md); CloudEvents, scheduling, and streaming remain deferred.
