# Open Workflow Agent

Open Workflow Agent is a configuration-driven runtime for running AI agents and Open Workflow 1.0.3 workflows without writing application-specific orchestration code.

You provide configuration, an optional workflow, optional knowledge, and optional tools. The runtime executes the same public contract on interchangeable engines such as ADK and LangGraph.

## Why use it?

Use Open Workflow Agent when you want to:

- run an AI agent from configuration;
- add knowledge, memory, tools, or workflows without changing application code;
- keep workflow definitions portable across supported execution engines;
- expose the runtime through a stable HTTP API;
- run the same packaged runtime in Docker, Kubernetes, or OpenShift.

Every invocation is executed as a workflow. If you do not provide a workflow, Open Workflow Agent generates a default workflow that calls the configured agent.

## Published container images

End users can pull the prebuilt runtime images from Docker Hub or GitHub Container Registry (GHCR). Docker Hub is used in end-user examples because it gives the shortest standard Docker image name; GHCR remains the canonical release/provenance registry.

Docker Hub:

```text
bzohdy/open-workflow-agent-adk:<tag>
bzohdy/open-workflow-agent-langgraph:<tag>
```

GHCR:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<tag>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<tag>
```

For the latest verified `main` build:

```bash
docker pull bzohdy/open-workflow-agent-adk:latest
docker pull bzohdy/open-workflow-agent-langgraph:latest
```

Every verified `main` build publishes `latest` and an immutable `sha-<sha>` tag to both registries. A formal SemVer release such as `v0.1.0` additionally publishes `0.1.0` and `0.1`. For production, prefer an explicit release version or image digest rather than `latest` once a formal release is available.

Images are published only after the GitHub Actions quality, engine, CTK, Docker, restart/resume, and persistence gates succeed. Both registries receive the same build and tags. OCI SBOM/provenance metadata is generated during the build, and GitHub build provenance attestations are published against the canonical GHCR image.

## 5-minute quick start

You need Docker only. No source checkout or Python environment is required.

### 1. Create runtime directories

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

The deterministic `fake/default` model lets you validate the runtime without an API key or paid model.

### 2. Pull and start one engine

ADK:

```bash
docker pull bzohdy/open-workflow-agent-adk:latest

docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-adk:latest
```

LangGraph uses the same configuration and mounts; only the image changes:

```bash
docker pull bzohdy/open-workflow-agent-langgraph:latest

docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-langgraph:latest
```

If you prefer GHCR, replace the image with the corresponding `ghcr.io/bassemzohdy/...` image and keep the same tag.

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
  -d '{"input":"Hello from Open Workflow Agent"}'
```

The response includes an `invocation_id`, `session_id`, status, and output.

## Configuration and mounted content

The published images use these standard paths:

```text
/config     runtime configuration and workflow definitions
/knowledge  read-only knowledge documents
/data       writable runtime state
```

Minimal configuration:

```yaml
model:
  provider: fake
  name: fake/default
```

A typical configuration can add an agent instruction, workflow, knowledge, memory, persistence, and tools without rebuilding the image.

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
```

Configuration precedence is:

```text
built-in defaults < YAML < environment variables
```

Environment variables use the `OWA__...` convention.

## Add a workflow

Create `config/workflow.yaml`:

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

Then point `config/agent.yaml` to it:

```yaml
workflow:
  path: /config/workflow.yaml
```

No image rebuild is required.

## Add knowledge

Place supported files in the host `knowledge/` directory. They are mounted into `/knowledge`, indexed by the common knowledge service, and exposed through `search_knowledge`.

The production images package the local FastEmbed/ONNX `all-MiniLM-L6-v2` embedding model, so mounted knowledge does not require a separate paid embedding API.

Reload manually with:

```bash
curl -X POST http://localhost:8080/v1/admin/knowledge/reload
```

## Real LLM providers

The standard ADK and LangGraph images bundle LiteLLM. Use `provider: litellm`; the `model.name` prefix selects the real provider.

| Provider | Model identifier | Main credential/connection |
| --- | --- | --- |
| OpenAI | `openai/<model>` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/<model>` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/<provider>/<model>` | `OPENROUTER_API_KEY` |
| Ollama | `ollama/<local-model>` | `model.options.api_base` |
| Other LiteLLM provider | `<provider-prefix>/<model>` | provider-specific LiteLLM variables |

For repository-local Compose usage:

```bash
cp .env.example .env
```

Then edit `.env`, for example:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=openai/<model-name>
OPENAI_API_KEY=replace-me
```

For Ollama running on the Docker host:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=ollama/<local-model-name>
OWA__MODEL__OPTIONS__API_BASE=http://host.docker.internal:11434
```

`compose.yaml` passes the local `.env` through to the runtime container so LiteLLM can read provider-specific variables. `.env` is ignored by git; use deployment secrets rather than `.env` in production.

For another LiteLLM provider, set `MODEL_NAME=<provider-prefix>/<model>` and add the provider-specific environment variables to `.env`. Custom/OpenAI-compatible endpoints can use `OWA__MODEL__OPTIONS__API_KEY`, `OWA__MODEL__OPTIONS__API_BASE`, and `OWA__MODEL__OPTIONS__API_VERSION`.

CI intentionally continues to use `fake/default`; building and testing the runtime never requires a paid model API.

See [configuration](docs/configuration.md#model) for OpenAI, Anthropic, OpenRouter, Ollama, direct Docker, Compose, and generic provider examples.

## Main API

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

Use `/v1/capabilities` to discover the capabilities of the selected engine/runtime version.

## Documentation

- [Getting started](docs/getting-started.md) — run a published image and invoke your first agent.
- [Configuration](docs/configuration.md) — runtime configuration and model-provider reference.
- [API guide](docs/api.md) — HTTP endpoints and request/response examples.
- [Deployment guide](docs/deployment.md) — Docker Hub/GHCR images, persistence, Docker, Kubernetes, and OpenShift.
- [Developer guide](docs/development.md) — source checkout, repository structure, tests, and contribution workflow.
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

ADK and LangGraph are implementation engines, not public application contracts. The same mounted configuration and workflow should remain portable when they use capabilities in the common profile.

## Development

Source checkout is only needed when contributing to or customizing the runtime. See [docs/development.md](docs/development.md) for local development, tests, and image builds.
