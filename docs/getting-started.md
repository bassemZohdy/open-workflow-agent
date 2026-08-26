# Getting Started

This guide gets Open Workflow Agent running locally and shows how to invoke the default agent, add a workflow, and mount knowledge.

## Prerequisites

For the Docker path you need:

- Git
- Docker with Compose support

For local Python development you also need Python 3.11+ and `uv`.

## Start with Docker Compose

Clone the repository:

```bash
git clone https://github.com/bassemZohdy/open-workflow-agent.git
cd open-workflow-agent
```

Copy the local environment template:

```bash
cp .env.example .env
```

The default environment uses the deterministic fake model. It does not require an API key.

Start ADK:

```bash
docker compose --profile adk up --build
```

Or start LangGraph:

```bash
docker compose --profile langgraph up --build
```

Only one engine profile is normally needed. The defaults expose ADK on port `8080` and LangGraph on `8081`.

## Verify the runtime

For ADK:

```bash
curl http://localhost:8080/health/live
curl http://localhost:8080/health/ready
curl http://localhost:8080/v1/capabilities
```

For LangGraph, replace `8080` with `8081`.

A ready runtime returns:

```json
{"status":"ok"}
```

## Invoke the default agent

No workflow file is required for the simplest case. Open Workflow Agent generates an internal default workflow that calls `agent:1.0.0@default`.

```bash
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":"Hello"}'
```

A successful response has this general shape:

```json
{
  "invocation_id": "...",
  "session_id": "...",
  "status": "completed",
  "output": {}
}
```

`input` may be any JSON value, not only a string.

## Add your own configuration

The default configuration path inside the runtime is:

```text
/config/agent.yaml
```

You can override it with:

```bash
OWA_CONFIG_FILE=/some/path/agent.yaml
```

Minimal configuration:

```yaml
model:
  provider: fake
  name: fake/default
```

Typical configuration:

```yaml
agent:
  name: support
  instruction: |
    Answer support questions using available knowledge and tools.

model:
  provider: fake
  name: fake/default

workflow:
  path: /config/workflow.yaml

knowledge:
  path: /knowledge

memory:
  enabled: auto
```

The provided Compose file configures the runtime through environment variables and does not bind-mount `/config`. For a YAML-based local run, build the selected image and mount the example files directly.

ADK example:

```bash
docker build -f docker/Dockerfile.adk -t owa-adk:local .
mkdir -p knowledge data

docker run --rm \
  -p 8080:8080 \
  -v "$(pwd)/examples/agent.yaml:/config/agent.yaml:ro" \
  -v "$(pwd)/examples/workflow.yaml:/config/workflow.yaml:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  owa-adk:local
```

For LangGraph, build `docker/Dockerfile.langgraph` and use the corresponding image name.

See [configuration.md](configuration.md) for all supported settings.

## Add an Open Workflow definition

Example `workflow.yaml`:

```yaml
document:
  dsl: '1.0.3'
  namespace: example
  name: support
  version: '1.0.0'

do:
  - answer:
      call: agent:1.0.0@default
      with:
        input: ${ .question }
```

Point the runtime to it:

```yaml
workflow:
  path: /config/workflow.yaml
```

Invoke it:

```bash
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":{"question":"What services are available?"}}'
```

Open Workflow 1.0.3 is the public workflow DSL. The internal execution plan is implementation detail and is not an authoring format.

## Add knowledge

The Compose setup mounts the repository's local `./knowledge` directory into `/knowledge` inside the runtime.

Create the directory if needed:

```bash
mkdir -p knowledge
```

Place supported documents there. The runtime hashes, parses, chunks, embeds, and indexes changed documents.

To force a reload:

```bash
curl -X POST http://localhost:8080/v1/admin/knowledge/reload
```

The production images include a local FastEmbed/ONNX embedding model so this path can work without an external embedding API.

## Persistence

Docker Compose starts PostgreSQL and configures the selected engine to use it.

For a standalone configuration:

```yaml
persistence:
  datasource: postgresql://user:password@host:5432/database
```

SQLite remains the reference local datasource when no PostgreSQL datasource is configured.

## Stop the stack

```bash
docker compose down
```

To remove persisted Compose volumes as well:

```bash
docker compose down -v
```

## Next steps

- Configure agents, models, memory, knowledge, and tools: [configuration.md](configuration.md)
- Integrate through HTTP: [api.md](api.md)
- Deploy with Docker/Kubernetes/OpenShift: [deployment.md](deployment.md)
- Work on the codebase: [development.md](development.md)
