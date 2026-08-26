# Getting Started

This guide runs Open Workflow Agent from the published container images. Docker Hub is the default in end-user examples; the same verified images and tags are also published to GitHub Container Registry (GHCR). You do not need to clone the repository or install Python to use the runtime.

## Prerequisites

You need Docker.

Published images:

Docker Hub:

```text
bzohdy/open-workflow-agent-adk:<tag>
bzohdy/open-workflow-agent-langgraph:<tag>
```

GHCR alternative:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<tag>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<tag>
```

Every verified `main` build publishes `latest` and an immutable `sha-<sha>` tag to both registries. Formal SemVer releases additionally publish the exact version and minor-series tags. For production, prefer an explicit release version or digest rather than `latest` once a formal release is available.

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
docker pull bzohdy/open-workflow-agent-adk:latest
```

LangGraph:

```bash
docker pull bzohdy/open-workflow-agent-langgraph:latest
```

You normally run one engine. Both use the same public configuration and mounted paths.

## Start ADK

```bash
docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-adk:latest
```

## Start LangGraph

```bash
docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-langgraph:latest
```

The host port is `8080` in both examples. Change only the image when switching engines. If you prefer GHCR, use the corresponding `ghcr.io/bassemzohdy/...` image with the same tag.

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
  bzohdy/open-workflow-agent-adk:latest
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

## Use a real LLM provider

The standard published images include LiteLLM. Set `provider: litellm`, then use the LiteLLM provider prefix in the model name.

Common patterns:

| Provider | Model name | Credential / connection |
| --- | --- | --- |
| OpenAI | `openai/<model>` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/<model>` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/<provider>/<model>` | `OPENROUTER_API_KEY` |
| Ollama | `ollama/<local-model>` | `api_base`, usually no key |
| Other | `<litellm-provider-prefix>/<model>` | provider-specific LiteLLM variables |

### OpenAI

```yaml
model:
  provider: litellm
  name: openai/<model-name>
```

```bash
export OPENAI_API_KEY=replace-me
```

### Anthropic

```yaml
model:
  provider: litellm
  name: anthropic/<model-name>
```

```bash
export ANTHROPIC_API_KEY=replace-me
```

### OpenRouter

```yaml
model:
  provider: litellm
  name: openrouter/<provider>/<model-name>
```

```bash
export OPENROUTER_API_KEY=replace-me
```

### Ollama

When Open Workflow Agent runs in Docker and Ollama runs on the host:

```yaml
model:
  provider: litellm
  name: ollama/<local-model-name>
  options:
    api_base: http://host.docker.internal:11434
```

When both run in Kubernetes/OpenShift, use the Ollama service URL instead of `host.docker.internal`.

### Repository Compose helper

For local source-based development, copy the provided environment template:

```bash
cp .env.example .env
```

Then choose a provider in `.env`. Example OpenAI configuration:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=openai/<model-name>
OPENAI_API_KEY=replace-me
```

OpenRouter:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=openrouter/<provider>/<model-name>
OPENROUTER_API_KEY=replace-me
```

Ollama:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=ollama/<local-model-name>
OWA__MODEL__OPTIONS__API_BASE=http://host.docker.internal:11434
```

Then start one engine:

```bash
docker compose --profile adk up --build
# or
docker compose --profile langgraph up --build
```

The repository Compose file passes `.env` through to the selected runtime container, so LiteLLM can read provider-specific environment variables. `.env` is ignored by git; production deployments should use a proper secret manager.

For any other LiteLLM provider, set `MODEL_NAME=<provider-prefix>/<model>` and add the credential/environment variables required by that provider. For custom/OpenAI-compatible endpoints you can also use `OWA__MODEL__OPTIONS__API_KEY`, `OWA__MODEL__OPTIONS__API_BASE`, and `OWA__MODEL__OPTIONS__API_VERSION`.

See [configuration.md](configuration.md#model) for the full provider guide.

No source checkout, Python installation, or custom image build is required for the standard LiteLLM path. CI continues to use `fake/default`, so project validation never needs a paid API key.

## Stop the runtime

If the container was started without `--rm`, stop it with:

```bash
docker stop open-workflow-agent
```

The examples use `--rm`, so Docker removes the container after it exits while the mounted `data/` directory remains on the host.

## Next steps

- Configure agents, models, memory, knowledge, and tools: [configuration.md](configuration.md)
- Integrate through HTTP: [api.md](api.md)
- Deploy with Docker Hub/GHCR/Docker/Kubernetes/OpenShift: [deployment.md](deployment.md)
- Contribute to the source code: [development.md](development.md)
