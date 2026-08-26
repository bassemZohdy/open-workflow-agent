# Open Workflow Agent

Open Workflow Agent is a configuration-driven runtime for running AI agents and Open Workflow 1.0.3 workflows without writing application-specific orchestration code.

You provide configuration, an optional workflow, optional knowledge, and optional tools. The runtime executes the same public contract on interchangeable engines such as ADK and LangGraph.

## Why use it?

Use Open Workflow Agent when you want to:

- run a simple AI agent from configuration only;
- add knowledge, memory, tools, or workflows without changing application code;
- keep workflow definitions portable across supported execution engines;
- expose the runtime through a stable HTTP API;
- run locally, in Docker, or on Kubernetes/OpenShift.

Every invocation is executed as a workflow. If you do not provide a workflow, Open Workflow Agent generates a default one that calls the configured agent.

## Published container images

Stable releases publish separate engine images to GitHub Container Registry after the full GitHub Actions CI gate succeeds for the tagged commit:

```bash
docker pull ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
docker pull ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
```

Each stable release also publishes the matching minor-series tag, `latest`, and an immutable source-SHA tag. Release images include SBOM/provenance metadata and GitHub build provenance attestations. See the [deployment guide](docs/deployment.md) for the release process and package visibility details.

## 5-minute quick start

### 1. Clone the repository

```bash
git clone https://github.com/bassemZohdy/open-workflow-agent.git
cd open-workflow-agent
```

### 2. Start one engine

The repository includes a deterministic `fake/default` model so you can validate the runtime without an API key or paid model.

```bash
cp .env.example .env
docker compose --profile adk up --build
```

Or start LangGraph instead:

```bash
docker compose --profile langgraph up --build
```

Default ports:

- ADK: `http://localhost:8080`
- LangGraph: `http://localhost:8081`

### 3. Check readiness

```bash
curl http://localhost:8080/health/ready
```

Expected response:

```json
{"status":"ok"}
```

### 4. Invoke the default agent

```bash
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{
    "input": "Hello from Open Workflow Agent"
  }'
```

The response includes an `invocation_id`, `session_id`, status, and output.

## Minimal configuration

Create `/config/agent.yaml` or set `OWA_CONFIG_FILE` to another path:

```yaml
model:
  provider: fake
  name: fake/default
```

A more typical configuration can include an agent instruction, workflow, knowledge, memory, persistence, tools, and server settings.

```yaml
agent:
  name: support
  instruction: |
    Answer questions using available knowledge and tools.

model:
  provider: fake
  name: fake/default

workflow:
  path: /config/workflow.yaml

knowledge:
  path: /knowledge

memory:
  enabled: auto

persistence:
  datasource: postgresql://owa:password@postgres:5432/owa
```

Configuration precedence is:

```text
built-in defaults < YAML < environment variables
```

Environment variables use the `OWA__...` convention, for example:

```bash
OWA__MODEL__NAME=fake/default
OWA__SERVER__PORT=8080
```

Unknown configuration properties are rejected.

## Add a workflow

Open Workflow 1.0.3 is the only public workflow DSL.

Example:

```yaml
document:
  dsl: '1.0.3'
  namespace: example
  name: support-agent
  version: '1.0.0'

do:
  - answer:
      call: agent:1.0.0@default
      with:
        input: ${ .question }
```

Then invoke it with:

```bash
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "question": "How can I renew my license?"
    }
  }'
```

## Add knowledge

Mount files into `/knowledge`. Supported documents are indexed by the common knowledge service and exposed to agents through the `search_knowledge` tool.

The production images package a local FastEmbed/ONNX `all-MiniLM-L6-v2` embedding model, so mounted knowledge does not require a separate paid embedding API.

You can reload knowledge manually with:

```bash
curl -X POST http://localhost:8080/v1/admin/knowledge/reload
```

## Use a real LLM provider

The core runtime supports LiteLLM through the optional `model` dependency. Provider credentials should be supplied through deployment secrets/environment variables, not placed directly in workflow files.

The current repository Dockerfiles are optimized for the default deterministic model and do not install the optional `model` extra. To use LiteLLM in a container, build an image that includes `open-workflow-agent[model]` (or add the equivalent locked dependency to the selected engine image).

## Main API

The runtime exposes:

```text
GET  /health/live
GET  /health/ready
GET  /v1/capabilities
POST /v1/invoke
POST /v1/invocations/{id}/resume
POST /v1/invocations/{id}/cancel
POST /v1/admin/knowledge/reload
POST /v1/events
GET  /v1/events/lifecycle
POST /v1/schedules
GET  /v1/schedules/{id}
POST /v1/schedules/{id}/cancel
```

Use `/v1/capabilities` to discover what the selected engine/runtime version supports rather than assuming every optional capability is portable.

## Documentation

- [Getting started](docs/getting-started.md) — run the project and invoke your first agent.
- [Configuration](docs/configuration.md) — complete runtime configuration reference.
- [API guide](docs/api.md) — HTTP endpoints and request/response examples.
- [Deployment guide](docs/deployment.md) — Docker, GHCR releases, persistence, Kubernetes/OpenShift considerations.
- [Developer guide](docs/development.md) — repository structure, tests, engine boundaries, and contribution workflow.
- [Project Definition](Project%20Definition.md) — authoritative architecture and product contract.
- [PROJECT.md](PROJECT.md) — verified implementation status.
- [TODO.md](TODO.md) — active backlog.
- [AGENTS.md](AGENTS.md) — mandatory contributor/AI-agent rules.

## Runtime model

```text
Configuration + Open Workflow + Knowledge + Memory + Tools
                         |
                         v
                 Open Workflow Agent
                         |
              +----------+----------+
              |                     |
             ADK                 LangGraph
```

ADK and LangGraph are implementation engines, not public application contracts. The same public configuration and workflow are intended to remain portable when they use capabilities in the common profile.

## Current scope

The runtime currently supports the common workflow/core capabilities already verified by the shared contract and CI suites, including deterministic workflow execution, agent/LLM catalog calls, knowledge, memory, persistence, HTTP/MCP/A2A/OpenAPI protocol adapters, lifecycle events, bounded scheduling, local sub-workflows, cancellation, and resume where supported.

The project does not claim full Open Workflow, MCP, A2A, or OpenAPI ecosystem conformance. Shell/script execution and external remote catalogs are disabled by default.

## Development

For local development:

```bash
uv sync --locked
uv run pytest -q
```

See [docs/development.md](docs/development.md) for the full validation matrix and engine-specific commands.
