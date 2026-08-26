# Deployment Guide

Open Workflow Agent is packaged as separate ADK and LangGraph runtime images. The public configuration contract is shared; selecting the image selects the execution engine.

## Published GHCR images

Versioned releases are published to GitHub Container Registry after the repository CI workflow passes for a `vX.Y.Z` release tag.

```bash
docker pull ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
docker pull ghcr.io/bassemzohdy/open-workflow-agent-langgraph:0.1.0
```

Stable releases publish these tags for each engine:

```text
0.1.0       exact project version
0.1         release series
latest      latest stable release
sha-...     verified source commit
```

The release workflow publishes OCI SBOM/provenance metadata and GitHub build provenance attestations alongside the images. It uses the repository-scoped `GITHUB_TOKEN`; no Docker Hub credential or long-lived registry token is required.

GitHub Package visibility is managed independently from repository visibility. After the first package publication, set each GHCR container package to **Public** in its package settings if anonymous pulls are required. Private packages require normal GHCR authentication.

## Release process

Normal pushes and pull requests run `.github/workflows/ci.yml`. That workflow validates root quality, native engine/contract tests, the selected CTK subset, Docker image size and container behavior, genuine stop/restart/resume, and PostgreSQL acceptance.

`.github/workflows/release.yml` listens for successful CI completion. It publishes only when the verified commit has an exact stable tag matching the project version:

```text
vX.Y.Z
```

For example, when the repository version is `0.1.0`:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The tag push starts CI. Only after that CI run succeeds does the release workflow:

1. verify the tag matches the root, core, ADK, and LangGraph package versions;
2. build and push the ADK and LangGraph images to GHCR;
3. publish version, series, `latest`, and commit-SHA tags;
4. attach SBOM/provenance and GitHub build attestations;
5. create the matching GitHub Release with generated release notes and image pull commands.

Release images currently target `linux/amd64`, matching the tested GitHub-hosted Ubuntu/OpenShift deployment baseline. Add another platform only after it has equivalent container and engine acceptance coverage.

## Image model

Build either engine independently:

```bash
docker build -f docker/Dockerfile.adk -t open-workflow-agent-adk:local .
```

```bash
docker build -f docker/Dockerfile.langgraph -t open-workflow-agent-langgraph:local .
```

The repository does not require both engine dependency graphs in one image.

Both images expose port `8080` and use these standard paths:

```text
/config     runtime configuration/workflow artifacts
/knowledge  mounted knowledge documents
/data       writable runtime state
```

## Docker Compose

The provided Compose stack starts PostgreSQL and one selected engine profile.

```bash
cp .env.example .env
```

ADK:

```bash
docker compose --profile adk up --build
```

LangGraph:

```bash
docker compose --profile langgraph up --build
```

Default host ports:

```text
PostgreSQL 5432
ADK        8080
LangGraph  8081
```

Change these in `.env` when required.

## Persistence

SQLite is the reference local datasource. PostgreSQL can be configured with:

```yaml
persistence:
  datasource: postgresql://user:password@host:5432/database
```

The common runtime and each engine keep isolated storage namespaces. Engine-native durable state is not shared between ADK and LangGraph.

For production use, provide credentials through your platform's secret mechanism instead of committing them to configuration files.

## Filesystem requirements

The containers are designed to:

- run as non-root;
- avoid requiring a fixed host UID;
- support a read-only root filesystem;
- write runtime data only to writable locations such as `/data` and `/tmp`;
- handle `SIGTERM` through the Python server process;
- avoid installing packages during startup.

The Compose configuration demonstrates a read-only root filesystem plus writable `/tmp` tmpfs and persistent data volumes.

## Knowledge model

Production images package the pinned local FastEmbed/ONNX `all-MiniLM-L6-v2` embedding model.

At startup the entrypoint stages the packaged model into writable `/tmp/fastembed` because FastEmbed creates small runtime metadata files.

Mount application knowledge read-only at `/knowledge` where practical.

## Real model providers

The core supports LiteLLM through the optional `model` dependency. The current engine Dockerfiles install native engine, knowledge, and PostgreSQL extras, but not the optional `model` extra.

If your deployment needs LiteLLM-backed models, extend/build the selected image with the locked model dependency included. Do not dynamically install it at container startup.

Provider API keys and credentials should come from Kubernetes/OpenShift Secrets, Docker secrets, or equivalent environment injection.

## Kubernetes/OpenShift baseline

A typical workload should configure:

```text
containerPort: 8080
readiness: GET /health/ready
liveness:  GET /health/live
config mount: /config
knowledge mount: /knowledge
state/PVC: /data
```

Recommended security posture:

- runAsNonRoot;
- allow arbitrary UID where the platform requires it;
- readOnlyRootFilesystem where possible;
- drop unnecessary Linux capabilities;
- use a writable volume/PVC for `/data`;
- use a bounded writable `/tmp`;
- restrict network egress to required model/tool/protocol endpoints;
- inject secrets separately from workflow definitions.

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

Environment variables override YAML, so deployment-specific values can be injected without rewriting the base configuration.

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

`/v1/capabilities` is useful for validating that the deployed engine advertises the expected runtime features.

## Image size and CI

The CI pipeline enforces a 2 GiB image-size gate for both engine images and exercises Docker health/invocation/knowledge acceptance.

The repository also validates PostgreSQL-backed persistence and genuine stop/restart/resume behavior across container boundaries for both engines.

## Production limitations to account for

Current bounded features should not be treated as broader infrastructure guarantees:

- generic event delivery is process-local and non-durable;
- lifecycle CloudEvents are a bounded snapshot, not a stream/broker;
- scheduling uses single-runtime ownership rather than distributed scheduler ownership;
- external workflow catalogs are disabled;
- shell/script/container execution is disabled;
- authentication is expected at the deployment boundary unless another trusted layer is introduced.

Always check `/v1/capabilities` and the current project status before relying on optional features.
