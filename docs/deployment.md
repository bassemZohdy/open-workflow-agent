# Deployment Guide

Open Workflow Agent is packaged as separate ADK and LangGraph runtime images. End-user and production deployments should consume the prebuilt images from GitHub Container Registry (GHCR). Building from source is a developer/customization workflow, not the normal deployment path.

## Published images

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<version>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<version>
```

Example stable release:

```bash
docker pull ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
docker pull ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
```

Stable releases also publish:

```text
0.1.0        exact release
0.1          minor series
latest       latest stable release
sha-<sha>    immutable verified source revision
```

For production, prefer the exact version tag or image digest rather than `latest`.

Each image is published only after the full GitHub Actions CI gate succeeds for the tagged commit. Release artifacts include OCI SBOM/provenance metadata and GitHub build provenance attestations.

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
  ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
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
  ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
```

No source checkout or Docker build is required.

## Docker Compose without source checkout

A deployment Compose file can reference GHCR directly:

```yaml
services:
  owa:
    image: ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
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
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
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

The standard ADK and LangGraph release images include the locked LiteLLM model runtime. A real provider can therefore be selected through the same mounted configuration without rebuilding the image.

Example:

```yaml
model:
  provider: litellm
  name: provider/model
  temperature: 0.1
```

Inject the credentials required by the selected LiteLLM provider through Docker, Kubernetes/OpenShift Secrets, or an equivalent secret mechanism. Do not place API keys in workflow definitions or committed configuration.

Example Docker pattern:

```bash
docker run -d --name open-workflow-agent-adk \
  -p 8080:8080 \
  -e PROVIDER_API_KEY \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
```

Replace `PROVIDER_API_KEY` with the actual environment variable required by the selected provider. CI and release acceptance continue to use `fake/default`, so no paid provider access is required to build or validate the images.

## Kubernetes/OpenShift

Use the published GHCR image directly in the workload specification.

ADK example:

```yaml
containers:
  - name: open-workflow-agent
    image: ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
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

For LangGraph, replace only the image name.

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
OWA__MODEL__NAME: provider/model
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

The repository keeps normal CI and release publication separate:

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

version tag vX.Y.Z on a green commit
        |
        v
   Release workflow
        |
        +-- verify tag == project version
        +-- build ADK image
        +-- build LangGraph image
        +-- push both to GHCR
        +-- attach SBOM/provenance + attestations
        +-- create GitHub Release
```

The release workflow uses the repository `GITHUB_TOKEN` with package-write permission. No Docker Hub account is required.

After the first package publication, set the GHCR package visibility to **Public** if anonymous pulls are required.

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
