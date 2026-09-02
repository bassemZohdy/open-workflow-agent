# Open Workflow Agent

[![CI](https://github.com/BassemZohdy/open-workflow-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/BassemZohdy/open-workflow-agent/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/BassemZohdy/open-workflow-agent?include_prereleases)](https://github.com/BassemZohdy/open-workflow-agent/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](pyproject.toml)

Open Workflow Agent is a configuration-driven runtime for running AI agents and Open Workflow 1.0.3 workflows without writing application-specific orchestration code.

You provide configuration, an optional workflow, optional knowledge, and optional tools. The runtime executes the same public contract on interchangeable engines (Google ADK and LangGraph today), so your workflows, knowledge, and integrations stay portable.

## Why use it?

Use Open Workflow Agent when you want to:

- run an AI agent from configuration — no application code, no rebuild to change workflow, knowledge, tools, or providers;
- expose your agent to other systems over **A2A 1.0.1** (Agent Card discovery, synchronous `message/send`, and task operations) behind a deployment-selected transport;
- run **durable human-in-the-loop approvals**: workflows pause, operators decide through protected endpoints, and decisions survive restarts;
- execute **sandboxed operations** (`run.shell`, `run.script`, `run.container`) through one framework-neutral contract with internal, Docker, or Kubernetes/OpenShift isolation backends;
- schedule workflows (`schedule.after`/`schedule.every`) with durable, restart-safe, at-least-once dispatch;
- publish lifecycle events as CloudEvents 1.0 with a bounded SSE stream;
- keep workflow definitions portable across execution engines — engines are implementation details, not your API.

Every invocation runs as a workflow. If you do not provide one, the runtime generates a default workflow that calls the configured agent.

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

For the current release:

```bash
docker pull bzohdy/open-workflow-agent-adk:0.1.0
docker pull bzohdy/open-workflow-agent-langgraph:0.1.0
```

Every verified `main` build publishes `latest` and an immutable `sha-<sha>` tag to both registries. The formal release `v0.1.0` additionally publishes `0.1.0` and `0.1`. For production, pin an explicit release version or image digest rather than `latest`.

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
docker pull bzohdy/open-workflow-agent-adk:0.1.0

docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-adk:0.1.0
```

LangGraph uses the same configuration and mounts; only the image changes:

```bash
docker pull bzohdy/open-workflow-agent-langgraph:0.1.0

docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-langgraph:0.1.0
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

### 5. What to try next

- Check what your deployment advertises: `GET /v1/capabilities`.
- Add knowledge and ask questions that use `search_knowledge` (below).
- Expose the agent over A2A: enable `a2a.enabled` and point any A2A client at the Agent Card.
- Pause a workflow for human approval and resume it — see [Approvals](docs/api.md#approvals-human-in-the-loop).

## Security model in one paragraph

The HTTP API is unauthenticated by default: deployments place authentication, authorization, and rate limiting at the edge (reverse proxy, gateway, or mesh). Inbound A2A supports deployment-configured named security profiles (`bearer` today, with `api_key`, OAuth2 client-credentials, and mTLS defined); secrets always come from deployment secret mechanisms, never workflow files. Shell/script/container execution, external catalogs, A2A, and approvals are all disabled until a deployment explicitly enables them, and every capability is reported truthfully by `GET /v1/capabilities`. See [SECURITY.md](SECURITY.md).

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

Environment variables use the `OWA__...` convention. Authentication and authorization are deployment configuration too; protocol adapters consume named security profiles, while raw credentials stay in environment/deployment secret mechanisms rather than workflow files. Traffic/rate/concurrency policy is kept separate from identity and authorization configuration.

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
GET  /v1/events/lifecycle/stream
GET  /v1/approvals
GET  /v1/approvals/{id}
POST /v1/approvals/{id}/decision
POST /v1/schedules
GET  /v1/schedules/{id}
POST /v1/schedules/{id}/cancel

# Inbound A2A 1.0.1 (optional; requires a2a.enabled)
GET  /.well-known/agent-card.json  (A2A 1.0.1 bounded profile, optional)
POST /a2a                      (A2A JSON-RPC binding: SendMessage, optional)
POST /a2a/message:send         (A2A HTTP+JSON binding, optional)
GET  /a2a/tasks/{task_id}      (A2A Task retrieval, optional)
POST /a2a/tasks/{task_id}:cancel (A2A Task cancellation, optional)
```

Use `/v1/capabilities` to discover the selected engine/runtime capabilities. The optional inbound A2A boundary targets stable A2A release `1.0.1` and advertises protocol version `1.0`; JSON-RPC uses `SendMessage`, `GetTask`, and `CancelTask`, while HTTP+JSON uses `/message:send`, `GET /tasks/{id}`, and `POST /tasks/{id}:cancel`. Inbound authentication optionally references a named `bearer` security profile through `a2a.security_profile`, whose declared principal (roles/scopes/audience) is then authorized per skill and operation through `a2a.authorization` allow rules (`message.send`, `tasks.get`, `tasks.cancel`). A2A tasks are a projection over runtime invocations: a waiting workflow appears as `input-required`, which is how durable approvals surface to A2A clients. Legacy A2A v0.3 discovery/method/Part forms are intentionally not retained. See [api.md](docs/api.md), [protocol baselines](docs/protocol-baselines.md), and [protocol/security decisions](docs/protocol-security-decisions.md).

## Sandbox execution

Executable Open Workflow operations (`run.shell`, `run.script`, `run.container`) are implemented but disabled by default; workflow definitions cannot enable them on their own. All execution goes through one framework-neutral `SandboxManager` contract shared by ADK and LangGraph — engines never create independent subprocess, Docker, or Kubernetes execution paths.

Three execution backends exist behind deployment configuration:

- **Internal sandbox** — controlled child-process execution inside the normal runtime deployment: dedicated workspace, bounded environment, input/output limits, timeout, cancellation, and cleanup. It is a controlled execution boundary, **not** a hard isolation boundary; it does not provide container, pod, VM, or microVM isolation.
- **Docker backend** — stronger-isolation external execution through a restricted Unix-socket controller so the main runtime never mounts an unrestricted Docker socket. Production acceptance is recorded green (see `PROJECT.md`).
- **Kubernetes/OpenShift backend** — controller-held cluster lifecycle permissions with deployment-owned namespace, ServiceAccount, image, resource, secret, and network-policy controls; the main runtime never receives cluster-wide permissions. Real-cluster Kubernetes acceptance is recorded in `PROJECT.md`; OpenShift-specific SCC/arbitrary-UID acceptance remains deferred and must not be advertised until verified.

See [Sandbox execution architecture](docs/sandbox-execution.md), the [external sandbox contract](docs/external-sandbox-contract.md), and [TODO.md](TODO.md) for the acceptance state of each backend.

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

ADK and LangGraph are implementation engines, not public application contracts. The same mounted configuration and workflow should remain portable when they use capabilities in the common profile. An optional Microsoft Agent Framework adapter exists for evaluation; it is not a production release target.

## Documentation

- [Getting started](docs/getting-started.md) — run a published image and invoke your first agent.
- [Configuration](docs/configuration.md) — runtime configuration and model-provider reference.
- [API guide](docs/api.md) — HTTP endpoints and request/response examples.
- [Protocol baselines](docs/protocol-baselines.md) — pinned latest-stable external protocol/specification versions.
- [Protocol and security decisions](docs/protocol-security-decisions.md) — version policy, security profiles, authorization vocabulary, traffic-policy separation, and A2A task/skill decisions.
- [Deployment guide](docs/deployment.md) — Docker Hub/GHCR images, persistence, Docker, Kubernetes, and OpenShift.
- [Developer guide](docs/development.md) — source checkout, repository structure, tests, and contribution workflow.
- [CI runners and governance](docs/ci-runners.md) — self-hosted Docker runner bootstrap, recovery, and branch-history governance.
- [Sandbox execution architecture](docs/sandbox-execution.md) — internal sandbox and external execution backends.
- [Troubleshooting and compatibility](docs/troubleshooting.md) — FAQ, upgrade notes, and version/compatibility matrix.
- [External sandbox contract](docs/external-sandbox-contract.md) — backend-neutral sandbox request/result/capability contract and controller boundaries.
- [A2A/streaming evaluation](docs/a2a-streaming-evaluation.md) — bounded lifecycle SSE baseline and deferred A2A streaming/push scope.
- [Engine adapter evaluation](docs/engine-adapter-evaluation.md) — how the Microsoft Agent Framework third engine was selected and its current deferral state.
- [Project Definition](Project%20Definition.md) — authoritative architecture and product contract.
- [PROJECT.md](PROJECT.md) — verified implementation status.
- [TODO.md](TODO.md) — active backlog.
- [AGENTS.md](AGENTS.md) — mandatory contributor/AI-agent rules.
- [Contributing](CONTRIBUTING.md) — contribution workflow.
- [Security policy](SECURITY.md) — private vulnerability reporting and security model.
- [Changelog](CHANGELOG.md) — notable changes.

## Development

Source checkout is only needed when contributing to or customizing the runtime. See [docs/development.md](docs/development.md) for local development, tests, and image builds.
