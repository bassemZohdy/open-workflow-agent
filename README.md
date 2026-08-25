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

## Containers and Status

Build independent images with `docker build -f docker/Dockerfile.adk .` or the LangGraph equivalent. Each image packages the pinned local FastEmbed/ONNX `all-MiniLM-L6-v2` model, runs as a non-root arbitrary UID, and has a 2 GiB CI size gate. Local image, mounted-knowledge, read-only-root, health, deterministic invocation, and stop/restart/resume acceptance passes. GitHub Actions reproduces root, engine, and Docker gates on Ubuntu; the first remote run remains the CI verification step.

No automated test requires paid model/API access. See [Project Definition.md](Project%20Definition.md) for the specification, [PROJECT.md](PROJECT.md) for verified working context, [TODO.md](TODO.md) for active work, and [AGENTS.md](AGENTS.md) for contributor rules.
