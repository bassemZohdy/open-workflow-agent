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

## External catalog egress

External Open Workflow function catalogs are disabled unless the deployment
configures `workflow.external_catalogs`. Keep catalog credentials in a secret
manager or environment variables referenced by `authentication`; never put
tokens in the workflow file. Egress must be limited to the exact HTTPS hosts
and, where possible, the exact `allowed_endpoints` configured for the alias.

Catalog functions must use an exact semantic version and are fetched before
readiness is announced. The runtime rejects redirects, private/link-local
destinations, oversized or malformed definitions, remote scripts, and failed
integrity pins. It does not use stale or unverified content after a failed
revalidation. `/v1/capabilities` exposes the sanitized catalog policy/state.

For a rollback, remove `use.catalogs` from the workflow and remove the external
catalog policy; local `workflow.catalog` child workflows remain available
without network access.

## Sandbox execution deployment boundary

Executable Open Workflow tasks are implemented but disabled by default. `run.script`, `run.shell`, and `run.container` are rejected until the deployment explicitly selects a sandbox backend; workflow definitions cannot enable them on their own. All execution goes through one framework-neutral `SandboxManager` contract, so engines never create independent subprocess, Docker, or Kubernetes execution paths.

The **internal** sandbox backend works inside a normal runtime deployment without Docker, Kubernetes, or OpenShift:

```text
Open Workflow Agent runtime
        |
        v
common SandboxManager
        |
        v
InternalSandboxBackend
        |
        v
controlled child process
```

It provides a dedicated workspace and bounded environment, input/output, timeout, cancellation, and cleanup — but it is a controlled execution boundary, **not** a hard isolation boundary: the child process still shares the runtime container/host kernel and namespaces. Do not treat internal-sandbox subprocess restrictions as equivalent to container, pod, VM, or microVM isolation.

The **external** backends provide stronger isolation and are selected through deployment configuration:

```text
SandboxManager
   |
   +-- InternalSandboxBackend
   +-- DockerSandboxBackend       production-accepted (restricted Unix-socket controller)
   +-- KubernetesSandboxBackend   merged; real-cluster acceptance pending
```

Deployment requirements for the external backends are strict:

- do not mount unrestricted `/var/run/docker.sock` into the Open Workflow Agent runtime;
- use the separate/restricted Docker controller (Unix-socket only) exposing only the minimum required sandbox operations;
- use a dedicated Kubernetes/OpenShift sandbox namespace/project and narrowly scoped ServiceAccount held by the controller, not the runtime;
- do not grant cluster-wide workload management permissions to the runtime or controller;
- prohibit privileged mode, host networking/namespaces, and host-path mounts for sandbox workloads;
- enforce approved images/registries, resource limits, ephemeral-storage bounds, network policy/egress restrictions, secret isolation, timeout, and cleanup/TTL behavior;
- advertise only isolation controls actually enforced by the selected backend.

Backend selection is deployment policy, not a workflow-specific Docker/Kubernetes extension. The same Open Workflow definition can move from the internal backend to an external backend when the required capability is supported. `/v1/capabilities` reports the sandbox block for the selected backend; Kubernetes/OpenShift execution is advertised only after real-cluster acceptance gates are green (see `TODO.md` B-006.3).

See [sandbox-execution.md](sandbox-execution.md) for the approved architecture and [external-sandbox-contract.md](external-sandbox-contract.md) for the backend-neutral contract.

## API exposure, rate limits, and controller trust boundary

### Unauthenticated endpoints and rate controls

The HTTP API (`/v1/invoke`, `/v1/events`, `/v1/schedules`, `/v1/invocations/*/resume|cancel`) has **no built-in authentication or rate limiting**. The runtime is designed to sit behind a deployment-controlled edge. Any exposure beyond loopback or a private network must place authentication, authorization, and rate/concurrency controls in front of it — for example a reverse proxy, API gateway, or service mesh that enforces:

- per-client and global request-rate limits on the unauthenticated endpoints;
- bounded request body sizes (the runtime also enforces its own input/output bounds);
- bounded concurrent in-flight invocations (each invocation executes a workflow and may consume model tokens, sandbox processes, or protocol connections);
- TLS termination and standard access logging.

Without such an edge, a single caller can saturate the runtime; treat this as a deployment prerequisite, not an optional hardening.

Approval endpoints (`/v1/approvals*`) additionally enforce the runtime's own bearer + operator-header guard when `approvals.enabled` is set; see [api.md](api.md).

### Sandbox controller Unix-socket trust boundary

The Docker sandbox backend splits trust in two:

```text
runtime container  --(dedicated Unix socket, shared volume)-->  controller container  --(Docker socket)-->  Docker
```

- The **runtime** never mounts the host Docker socket. It talks to the controller over a dedicated Unix socket (default `/run/owa-sandbox/controller.sock`).
- The **controller** is the only component holding Docker credentials. It exposes no TCP listener and only the minimum sandbox operations.
- The dedicated socket is group-0 accessible: any code that executes inside the runtime container (group `0`) can drive the controller, and through it the approved Docker operations. The boundary therefore assumes the runtime container's code and dependencies are trusted. It limits blast radius and policy bypass paths (approved-image digests, non-root workloads, denied networking) — it is not a defense against arbitrary code execution inside the runtime itself. For a stronger boundary, run the controller with a socket proxy or rootless Docker, or use the Kubernetes controller with namespace-scoped RBAC.

### Controller images, deployment, and upgrades

Both restricted controllers are published by the release pipeline alongside the engine images:

```text
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller:<tag>
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller:<tag>
```

They receive the same `latest`, `sha-<sha>`, and (on tagged releases) version tags, OCI SBOM/provenance metadata, GHCR provenance attestations, and the Trivy scan gate. Locally, `compose.sandbox.yaml` shows the reference wiring (controller + shared socket volume). For upgrades: the controller/runtime socket protocol is private to a release — pull the controller image tag that matches the runtime image tag (for example the same `sha-<sha>`), then restart both together. The Kubernetes controller runs as a loopback sidecar of the runtime (`sandbox.kubernetes.controller_url` defaults to `http://127.0.0.1:8090`); the Docker controller runs as a separate container sharing only the socket volume.

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

Reference manifests that follow this guidance are provided in the repository and validated in CI with `kubeconform -strict`:

- `deploy/kubernetes/runtime.yaml` — Namespace, PersistentVolumeClaim, Deployment (non-root, read-only root filesystem, dropped capabilities, liveness/readiness/startup probes against `/health/live` and `/health/ready`), and ClusterIP Service for the ADK image.
- `deploy/openshift/runtime.yaml` — the same layout for the LangGraph image without a pinned UID (OpenShift's restricted-v2 SCC allocates an arbitrary non-root UID, which the images support).
- `deploy/kubernetes/sandbox-boundary.yaml` and `deploy/openshift/sandbox-boundary.yaml` — the restricted sandbox-controller namespace, ServiceAccounts, and RBAC boundary (see [sandbox-execution.md](sandbox-execution.md)).

`replicas: 1` with a `Recreate` strategy is intentional: the scheduler uses single-runtime ownership and the default deployment keeps durable state on one `ReadWriteOnce` volume. Horizontal scale-out requires external coordination of scheduler ownership and state and is not part of the bounded profile.

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
ADK        266 MB
LangGraph  248 MB
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

- generic event delivery is process-local and non-durable (durable approval state/replay is a separate bounded mechanism);
- lifecycle CloudEvents are a bounded snapshot and the SSE endpoint is bounded to lifecycle events, not a general output stream/broker;
- scheduling uses single-runtime ownership rather than distributed scheduler ownership;
- external workflow catalogs are disabled unless a deployment explicitly configures catalog trust policy (HTTPS-only, pinned, allowlisted; see the external catalog section above);
- shell/script execution uses the internal sandbox only when enabled by deployment configuration and is not a hard isolation boundary; container execution uses the selected external backend (Docker accepted; Kubernetes/OpenShift pending real-cluster acceptance);
- authentication is expected at the deployment boundary unless another trusted layer is introduced.

Always check `/v1/capabilities` and the current project status before relying on optional features.

## Building from source

Building the Dockerfiles directly is intentionally documented only for developers and custom image maintainers. See [development.md](development.md) if you need to modify dependencies, change runtime code, or build a custom variant.
