# Getting Started

This guide runs Open Workflow Agent from the published GitHub Container Registry images. You do not need to clone the repository or install Python to use the runtime.

## Prerequisites

You need:

- Docker;
- a published Open Workflow Agent image version.

The examples below use `0.1.0`. For production, pin an explicit release version rather than `latest`.

Published images:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<version>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<version>
```

## Create a local runtime directory

```bash
mkdir -p owa/config owa/knowledge owa/data
cd owa
```

Create `config/agent.yaml`:

```yaml
model:
  provider: fake
  name: fake/default
```

The built-in deterministic fake model requires no API key and is useful for validating the runtime and its configuration.

## Pull an engine image

ADK:

```bash
docker pull ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
```

LangGraph:

```bash
docker pull ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
```

You normally run one engine. Both use the same public configuration and mounted paths.

## Start ADK

```bash
docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
```

## Start LangGraph

```bash
docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
```

The host port is `8080` in both examples. Change only the image when switching engines.

## Verify the runtime

```bash
curl http://localhost:8080/health/live
curl http://localhost:8080/health/ready
curl http://localhost:8080/v1/capabilities
```

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

`input` may be any JSON value.

## Add your own configuration

The default configuration path inside the image is:

```text
/config/agent.yaml
```

The host `config/` directory in the examples is mounted read-only at `/config`, so configuration changes do not require rebuilding the image.

Typical `config/agent.yaml`:

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

Environment variables override YAML. For example:

```bash
docker run --rm \
  -p 8080:8080 \
  -e OWA__SERVER__PORT=8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
```

See [configuration.md](configuration.md) for all supported settings.

## Add an Open Workflow definition

Create `config/workflow.yaml`:

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

Point `config/agent.yaml` to it:

```yaml
workflow:
  path: /config/workflow.yaml
```

Restart the container if the configured workflow is loaded at startup. The image itself does not change.

Invoke it:

```bash
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":{"question":"What services are available?"}}'
```

Open Workflow 1.0.3 is the public workflow DSL. The internal execution plan is implementation detail.

## Add knowledge

Put supported documents in the host `knowledge/` directory. It is mounted at `/knowledge` inside the container.

The runtime hashes, parses, chunks, embeds, and indexes changed documents. The published engine images package the local FastEmbed/ONNX `all-MiniLM-L6-v2` embedding model, so this does not require a separate paid embedding API.

Reload manually:

```bash
curl -X POST http://localhost:8080/v1/admin/knowledge/reload
```

Persistent knowledge metadata is stored under `/data`, which is why the host `data/` directory is writable.

## Persistence

SQLite is the simplest standalone persistence path and uses `/data`.

For PostgreSQL, configure:

```yaml
persistence:
  datasource: postgresql://user:password@host:5432/database
```

Prefer injecting the real datasource through an environment variable or secret:

```bash
-e 'OWA__PERSISTENCE__DATASOURCE=postgresql://user:password@host:5432/database'
```

The runtime and engine-specific durable state use isolated namespaces; ADK and LangGraph do not share checkpoint representations.

## Real LLM providers

The runtime has a LiteLLM adapter, but the current base published engine images do not install the optional `model` extra. Therefore the image-first path currently supports the built-in deterministic model directly; a model-enabled published variant is needed before a real LiteLLM provider can be used without rebuilding.

Provider secrets must be supplied through deployment secrets/environment variables rather than workflow files.

## Stop the runtime

If the container was started without `--rm`, stop it with:

```bash
docker stop open-workflow-agent
```

The examples use `--rm`, so Docker removes the container after it exits while the mounted `data/` directory remains on the host.

## Next steps

- Configure agents, models, memory, knowledge, and tools: [configuration.md](configuration.md)
- Integrate through HTTP: [api.md](api.md)
- Deploy with GHCR/Docker/Kubernetes/OpenShift: [deployment.md](deployment.md)
- Contribute to the source code: [development.md](development.md)
