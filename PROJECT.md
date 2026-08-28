# Project Context

## Source of Truth

- `Project Definition.md` — architecture/product contract.
- `PROJECT.md` — verified implementation and release state.
- `TODO.md` — active and intentionally deferred backlog.
- `AGENTS.md` — mandatory repository/contributor rules.

## Current Phase — 2026-08-29

`v0.1.0` is the current formal release. `main` contains additional unreleased pre-stable work.

The project has moved beyond the original “bounded A2A deferred” state: a bounded inbound A2A 1.0 profile is implemented and verified. The active work is now protocol-baseline completion, reusable security policy, and the next bounded A2A profile built around A2A Tasks.

No broad A2A, MCP, OpenAPI, CloudEvents, Open Workflow, OpenShift, or multi-engine conformance claim is made beyond the exact tested capability/profile boundaries.

## Architecture

The runtime pipeline is:

```text
load
  -> official Open Workflow schema validation
  -> Portable Profile capability gate
  -> normalize
  -> immutable canonical execution plan
  -> selected engine
```

Core is framework-neutral. Engine adapters own framework-native construction, execution, checkpoints, and resume behavior.

Production runtime engines:

```text
ADK
LangGraph
```

Optional evaluation adapter:

```text
Microsoft Agent Framework
```

The Agent Framework adapter is CI-covered but is not a production image/release target.

Every request executes a workflow. If no workflow is supplied, the runtime generates the default one-task workflow.

Common protocol/runtime services remain in core where practical:

```text
HTTP
MCP
A2A
OpenAPI
knowledge
memory
invocation metadata
approvals
scheduling
sandbox policy
lifecycle events
```

Engine-native state never becomes the public API contract.

## Runtime and Image State

Standard ADK and LangGraph images include:

- LiteLLM for configured real model providers;
- deterministic `fake/default` for tests and no-key validation;
- FastEmbed/ONNX local knowledge embeddings using `sentence-transformers/all-MiniLM-L6-v2`;
- SQLite reference persistence;
- PostgreSQL support behind locked extras;
- health/readiness endpoints;
- arbitrary-UID/read-only-root-compatible runtime behavior.

Verified image sizes after the 2026-08-27 dependency refresh:

```text
ADK        ~266 MB decimal
LangGraph  ~248 MB decimal
```

The multi-gigabyte Torch/CUDA dependency path is not used by the standard images.

## Formal Release v0.1.0 — 2026-08-28

Release commit:

```text
c47cb86
```

Release workflow:

```text
33136714445
```

Companion acceptance for the release head:

| Workflow | Run | Result |
| --- | --- | --- |
| CI | `33136592588` | green |
| Security | `33136597832` | green |
| External Sandbox CI | `33136592592` | green |
| PostgreSQL CI | `33136592632` | green |
| Release | `33136714445` | success |

Published runtime image digests for `0.1.0`:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk
sha256:4d89ffaa88207488fec4b128e1e728282cca701f0e31330c7314c1606235cf36

ghcr.io/bassemzohdy/open-workflow-agent-langgraph
sha256:add38f52c062a01ab81c61962ab609a728e62a367818cc77bff19a6a720d2a89

docker.io/bzohdy/open-workflow-agent-adk
sha256:4d89ffaa88207488fec4b128e1e728282cca701f0e31330c7314c1606235cf36

docker.io/bzohdy/open-workflow-agent-langgraph
sha256:add38f52c062a01ab81c61962ab609a728e62a367818cc77bff19a6a720d2a89
```

Sandbox-controller images are published to GHCR. Docker Hub mirroring for controller images is wired for future releases.

The release pipeline includes Trivy image scanning, OCI SBOM/provenance metadata, and GHCR build provenance attestations.

## Verified Kubernetes Sandbox Acceptance — 2026-08-28

Kubernetes real-cluster acceptance is green on kind with Kubernetes 1.37 and Calico NetworkPolicy enforcement.

Verified behavior includes:

- end-to-end `run.container` execution;
- sandbox workload runs as numeric non-root `65532:65532`;
- timeout maps to `sandbox_timeout` and cleans the Job;
- cancellation returns `cancelled` and cleans the Job;
- ambiguous controller failure remains bounded by deadline/TTL cleanup;
- workload secrets are injected by `secretKeyRef` and do not appear in responses/logs;
- controller RBAC is namespace-bounded and forbids secret reads, pod creation, cross-namespace Jobs, and cluster-scope operations;
- default-deny NetworkPolicy egress is enforced.

OpenShift-specific SCC/security-context/arbitrary-UID acceptance remains deferred until an OpenShift cluster is available.

## Bounded Lifecycle Streaming

Common lifecycle SSE is implemented at:

```text
GET /v1/events/lifecycle/stream
```

It is a bounded engine-neutral observation stream over common lifecycle CloudEvents. It is not token/output streaming and it is not itself an A2A protocol binding.

Implemented guarantees include:

- Server-Sent Events;
- bounded replay with `Last-Event-ID`;
- explicit failure when a replay cursor is no longer retained;
- bounded subscriber queues and subscriber count;
- event, byte, queue, and lifetime limits;
- sanitized common lifecycle payloads;
- no engine checkpoint or native stream objects;
- disconnecting an observer does not cancel the invocation.

## Current A2A State

Pinned baseline:

```text
A2A release:   1.0.1
protocol:      1.0
```

The bounded inbound A2A profile is implemented, disabled by default, and deployment-controlled.

Implemented boundary:

```text
GET  /.well-known/agent-card.json
POST <configured A2A path>                 JSON-RPC SendMessage
POST <configured A2A path>/message:send    HTTP+JSON
```

Selectable transports:

```text
jsonrpc    default
http_json
```

Implemented behavior:

- stable A2A v1 Agent Card metadata with `supportedInterfaces`;
- synchronous bounded `SendMessage`;
- v1 Part/message shapes;
- deployment-configured public base URL;
- optional temporary bearer authentication;
- bounded request/message sizes;
- sanitized transport-specific error mapping;
- `features.a2a` capability advertisement limited to implemented behavior;
- no legacy A2A v0.3 aliases.

Remote verification for the bounded A2A slice at commit `a20ef51`:

| Workflow | Run | Result |
| --- | --- | --- |
| CI | `33156173321` | green |
| External Sandbox | `33156173327` | green |
| PostgreSQL | `33156173308` | green |
| Release/latest refresh | `33156335003` | success |

The formal `v0.1.0` release remains pinned to `c47cb86`; the A2A work above is part of later unreleased `main` state.

## A2A Work That Is Not Complete

The next bounded profile is intentionally built on common OWA invocation state rather than a separate A2A execution engine.

Target relationship:

```text
A2A Task
   |
   v
OWA invocation_id / ExecutionHandle
   |
   v
selected engine native execution state
```

Planned order:

```text
shared named security profiles
  -> deployment-declared skills
  -> A2A Task projection
  -> task retrieval/cancellation
  -> waiting/input-required/resume mapping
  -> spec-native async behavior
  -> A2A streaming/resubscription
  -> interoperability/conformance gates
```

The remaining streaming dependency is therefore the portable A2A Task/message/artifact lifecycle contract, not direct ADK/LangGraph stream support.

Push notifications remain separately deferred because they create an outbound callback trust boundary requiring allowlisting, TLS identity verification, callback authentication, SSRF protection, replay/idempotency controls, bounded retries/dead-letter handling, and secret-safe observability.

Delegated-user identity, token exchange, and consent remain deployment/identity-platform concerns and do not block Task support.

## Protocol Baseline Work

Pinned stable baselines:

| Protocol/specification | Baseline | Current position |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | implemented subset / Portable Profile |
| A2A Protocol | `1.0.1` | bounded inbound v1 profile implemented; Task/conformance expansion active backlog |
| Model Context Protocol | `2026-07-28` | common client migrated; final audit/compatibility verification active |
| OpenAPI Specification | `3.2.0` | bounded operation adapter; no full-parser/conformance claim |
| CloudEvents | `1.0.2` | bounded lifecycle behavior; compatibility verification active |
| AsyncAPI | `3.1.0` | future binding baseline |

Protocol versions are reviewed and pinned; upstream stable releases do not automatically change runtime support or capability advertisement.

## Security Architecture State

Target common security profile types:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

The model is documented but not yet implemented as the shared runtime configuration layer.

Current protocol-specific bearer fields are temporary pre-stable implementation details and are not compatibility contracts.

Authorization terminology is standardized as:

```text
principal / identity
role
scope
permission / action
resource
audience
```

Traffic/rate/concurrency policy remains a separate deployment concern from authentication/authorization.

OWA does not become an identity provider. OAuth2/OIDC federation, user delegation, token exchange, and consent remain external identity-platform responsibilities.

## Persistence and State Boundaries

The runtime preserves distinct lifecycles for:

```text
knowledge
memory
session
common invocation metadata
approvals
schedules
sandbox executions
engine-native checkpoints/state
```

SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented with isolated namespaces.

Engine-native checkpoint state is never exposed as a public resume contract.

## Sandbox State

Executable operations are disabled by default and must pass common capability/policy gates.

Backends:

```text
internal process sandbox
restricted Docker controller
restricted Kubernetes/OpenShift controller
```

All engines route executable workflow operations through the common `SandboxManager` contract.

The internal sandbox is a controlled execution boundary, not a hard isolation boundary. Stronger isolation uses external Docker/Kubernetes backends.

## Current Active Backlog

The authoritative ordered backlog is `TODO.md`. Current priorities are:

1. complete pinned protocol compatibility/advertisement gates;
2. implement reusable named security profiles and authorization checks;
3. keep traffic policy separate from security policy;
4. implement deployment-declared A2A skills;
5. implement A2A Task projection, retrieval/cancellation, and waiting/resume mapping;
6. implement A2A streaming/resubscription only after Task state is stable;
7. add interoperability/conformance coverage before broadening A2A claims.

## Intentionally Deferred

- OpenShift-specific sandbox acceptance until an OpenShift cluster is available.
- A2A push notifications.
- Broad/full A2A conformance claim until Task/streaming/interoperability gates are green.
- Microsoft Agent Framework production image/release status.
- Multi-tenancy.
- Delegated-user identity/token exchange/consent inside OWA.

## Verification Rules

A capability is considered shipped only when applicable deterministic tests and required acceptance gates are green.

Core must remain framework-neutral. Shared contract tests are the portability proof.

Protocol baseline changes are compatibility/security changes and must not be treated as ordinary dependency bumps.

## Key Commands

```text
uv sync --locked
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q --cov=core/src/open_workflow_agent --cov-fail-under=80
uv build
uv build --directory core
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
uv run --directory engines/agent-framework --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/agent_framework -q
```
