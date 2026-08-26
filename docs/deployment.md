# Deployment Guide

Open Workflow Agent is packaged as separate ADK and LangGraph runtime images. End-user and production deployments should consume the prebuilt images from Docker Hub or GitHub Container Registry (GHCR). Docker Hub is used in end-user examples for convenience; GHCR remains the canonical release/provenance registry. Building from source is a developer/customization workflow, not the normal deployment path.

## Published images

Docker Hub:

```text
bzohdy/open-workflow-agent-adk:<tag>
bzohdy/open-workflow-agent-langgraph:<tag>
```

GitHub Container Registry:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<tag>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<tag>
```

Pull the latest verified `main` build from Docker Hub:

```bash
docker pull bzohdy/open-workflow-agent-adk:latest
docker pull bzohdy/open-workflow-agent-langgraph:latest
```

The corresponding GHCR pulls are:

```bash
docker pull ghcr.io/bassemzohdy/open-workflow-agent-adk:latest
docker pull ghcr.io/bassemzohdy/open-workflow-agent-langgraph:latest
```

Publication tags are:

```text
latest       latest verified main build
sha-<sha>    immutable verified source revision
0.1.0        exact SemVer release, when a matching v0.1.0 release is published
0.1          minor series, when a matching SemVer release is published
```

For production, prefer an exact SemVer tag or image digest rather than `latest` once a formal release is available. Both registries receive the same verified build and tags.

Images are published only after the full GitHub Actions CI gate succeeds. OCI SBOM/provenance metadata is generated for the published build, and GitHub build provenance attestations are attached to the canonical GHCR image.

## Runtime paths

Both engine images expose port `8080` and use the same public paths:

```text
/config     runtime configuration and workflow artifacts
/knowledge  mounted knowledge documents
/data       writable runtime state
```

Selecting the image selects the engine; application configuration should not require an engine field.

## Standalone Docker deployment

Create local directories:

```bash
mkdir -p config knowledge data
```

Create `config/agent.yaml`:

```yaml
model:
  provider: fake
  name: fake/default
```

Run ADK:

```bash
docker run -d --name open-workflow-agent-adk \
  -p 8080:8080 \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-adk:latest
```

Run LangGraph instead by changing only the container/image name:

```bash
docker run -d --name open-workflow-agent-langgraph \
  -p 8080:8080 \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  bzohdy/open-workflow-agent-langgraph:latest
```

No source checkout or Docker build is required. Replace `latest` with a release tag or digest for production deployments when available. Use the corresponding `ghcr.io/bassemzohdy/...` image if GHCR is preferred by your platform or supply-chain policy.

## Docker Compose without source checkout

A deployment Compose file can reference Docker Hub directly:

```yaml
services:
  owa:
    image: bzohdy/open-workflow-agent-adk:latest
    ports:
      - "8080:8080"
    read_only: true
    tmpfs:
      - /tmp:size=256m
    volumes:
      - ./config:/config:ro
      - ./knowledge:/knowledge:ro
      - ./data:/data
```

Switch to LangGraph by changing `image` to:

```text
bzohdy/open-workflow-agent-langgraph:latest
```

GHCR can be used instead with the same tag:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:latest
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:latest
```

The repository's developer Compose file may build from source for contributor testing; that is not required for normal users.

## Persistence

SQLite is the reference local datasource and works with the writable `/data` mount.

PostgreSQL can be configured with:

```yaml
persistence:
  datasource: postgresql://user:password@host:5432/database
```

For production, inject the datasource using a secret/environment variable instead of committing credentials:

```text
OWA__PERSISTENCE__DATASOURCE=postgresql://...
```

The common runtime and each engine keep isolated storage namespaces. Engine-native durable state is not shared between ADK and LangGraph.

## Knowledge

The published images package the pinned local FastEmbed/ONNX `all-MiniLM-L6-v2` embedding model.

At startup the runtime stages the packaged model into writable `/tmp/fastembed` because FastEmbed creates small runtime metadata files. Mount application knowledge read-only at `/knowledge` where practical.

No separate paid embedding API is required for the default knowledge path.

## Real model providers

The standard ADK and LangGraph published images include LiteLLM. Use `provider: litellm` and select the upstream provider through the model prefix.

| Provider | `model.name` | Secret / endpoint |
| --- | --- | --- |
| OpenAI | `openai/<model>` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/<model>` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/<provider>/<model>` | `OPENROUTER_API_KEY` |
| Ollama | `ollama/<local-model>` | `model.options.api_base` |
| Other LiteLLM provider | `<provider-prefix>/<model>` | provider-specific LiteLLM variables |

Example OpenAI configuration:

```yaml
model:
  provider: litellm
  name: openai/<model-name>
```

Inject the key as a deployment secret/environment variable:

```text
OPENAI_API_KEY=...
```

Anthropic uses the same runtime configuration shape with `anthropic/<model>` and `ANTHROPIC_API_KEY`. OpenRouter uses `openrouter/<provider>/<model>` and `OPENROUTER_API_KEY`.

For Ollama running on a Docker host:

```yaml
model:
  provider: litellm
  name: ollama/<local-model-name>
  options:
    api_base: http://host.docker.internal:11434
```

For Kubernetes/OpenShift, use the Ollama service URL reachable from the pod instead of `host.docker.internal`.

For other LiteLLM providers, set `model.name` to the provider prefix/model identifier and inject the provider-specific environment variables required by LiteLLM. For custom/OpenAI-compatible endpoints, `model.options` can carry `api_key`, `api_base`, and `api_version`; prefer supplying those through `OWA__MODEL__OPTIONS__...` environment variables/secrets rather than committed YAML.

Repository-local Compose usage is documented by `.env.example`. It passes the local `.env` through to the runtime container so provider-specific LiteLLM environment variables are available:

```bash
cp .env.example .env
```

Example:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=openrouter/<provider>/<model-name>
OPENROUTER_API_KEY=replace-me
```

This `.env` pattern is for local development. Production deployments should use Kubernetes/OpenShift Secrets, Docker secrets, or another proper secret-management mechanism. CI and release acceptance continue to use `fake/default`, so no paid provider access is required to build or validate the images.

See [configuration.md](configuration.md#model) for detailed OpenAI, Anthropic, OpenRouter, Ollama, Compose, and generic provider examples.

## Kubernetes/OpenShift

Use either published registry in the workload specification. Docker Hub is shown below for consistency with end-user examples; replace it with the corresponding GHCR image if your organization prefers the canonical provenance registry.

ADK example:

```yaml
containers:
  - name: open-workflow-agent
    image: bzohdy/open-workflow-agent-adk:latest
    ports:
      - containerPort: 8080
    readinessProbe:
      httpGet:
        path: /health/ready
        port: 8080
    livenessProbe:
      httpGet:
        path: /health/live
        port: 8080
    volumeMounts:
      - name: config
        mountPath: /config
        readOnly: true
      - name: knowledge
        mountPath: /knowledge
        readOnly: true
      - name: data
        mountPath: /data
```

For LangGraph, replace only the image name. For production, pin the workload to a SemVer tag or digest when available.

Recommended security posture:

- run as non-root;
- allow an arbitrary UID where OpenShift requires it;
- use a read-only root filesystem where possible;
- drop unnecessary Linux capabilities;
- provide writable `/data` and bounded `/tmp`;
- restrict network egress to required model/tool/protocol endpoints;
- inject credentials separately from workflow definitions.

## Configuration and secrets

Recommended split:

```text
ConfigMap / mounted config
  agent.yaml
  workflow.yaml

Read-only content volume
  /knowledge

Secret manager
  database password
  provider API keys
  protocol credentials

Persistent volume / PostgreSQL
  durable state
```

Environment variables override YAML, so deployment-specific values can be injected without rebuilding the image.

Example:

```yaml
OWA__SERVER__PORT: "8080"
OWA__MODEL__PROVIDER: litellm
OWA__MODEL__NAME: openai/<model-name>
OPENAI_API_KEY: <from-secret>
OWA__PERSISTENCE__DATASOURCE: postgresql://...
```

## Health and rollout checks

Before sending traffic, verify:

```bash
curl http://host:8080/health/live
curl http://host:8080/health/ready
curl http://host:8080/v1/capabilities
```

`/v1/capabilities` is the authoritative runtime capability view for the selected engine/version.

## Release and registry workflow

The repository keeps normal CI and publication separate:

```text
push / pull request
        |
        v
      CI
        |
        +-- root quality gates
        +-- ADK/LangGraph native + contracts + CTK
        +-- Docker build/size/health/knowledge
        +-- stop/restart/resume
        +-- PostgreSQL acceptance

successful push CI on main
        |
        v
   Release workflow
        |
        +-- resolve project version and optional matching SemVer tag
        +-- build ADK image once
        +-- build LangGraph image once
        +-- push each build to Docker Hub and GHCR
        +-- publish latest + sha-<sha>
        +-- if matching vX.Y.Z exists: also publish X.Y.Z + X.Y
        +-- attach SBOM/provenance; attest canonical GHCR image
        +-- create GitHub Release for matching SemVer release
```

Docker Hub publication uses the repository variable `DOCKERHUB_USERNAME` and repository secret `DOCKERHUB_TOKEN`. GHCR publication uses the repository `GITHUB_TOKEN` with package-write permission.

Docker Hub is the convenient end-user distribution path. GHCR is retained as the canonical provenance/attestation registry. Both receive the same image build and corresponding tags.

After the first GHCR package publication, set the GHCR package visibility to **Public** if anonymous pulls are required.

## Image size and verification

CI enforces a 2 GiB hard image-size limit for each engine image. With LiteLLM, FastEmbed/ONNX, PostgreSQL support, and the selected engine runtime bundled, the verified images are approximately:

```text
ADK        575 MB
LangGraph  515 MB
```

They remain well below the gate and avoid the earlier Torch/CUDA dependency bloat.

CI also validates:

- non-root/arbitrary-UID startup;
- read-only root filesystem behavior;
- liveness/readiness;
- capabilities;
- deterministic invocation;
- LiteLLM importability in the built image;
- mounted knowledge and reload behavior;
- genuine stop → restart → resume across container boundaries;
- PostgreSQL-backed persistence.

## Production limitations

Current bounded features should not be interpreted as broader infrastructure guarantees:

- generic event delivery is process-local and non-durable;
- lifecycle CloudEvents are a bounded snapshot, not a stream/broker;
- scheduling uses single-runtime ownership rather than distributed scheduler ownership;
- external workflow catalogs are disabled;
- shell/script/container execution is disabled;
- authentication is expected at the deployment boundary unless another trusted layer is introduced.

Always check `/v1/capabilities` and the current project status before relying on optional features.

## Building from source

Building the Dockerfiles directly is intentionally documented only for developers and custom image maintainers. See [development.md](development.md) if you need to modify dependencies, change runtime code, or build a custom variant.
