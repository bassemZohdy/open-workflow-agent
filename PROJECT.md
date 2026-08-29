# Project Context

## Source of Truth

- `Project Definition.md` — architecture/product contract.
- `PROJECT.md` — verified implementation and release state.
- `TODO.md` — active and intentionally deferred backlog.
- `AGENTS.md` — mandatory repository/contributor rules.

## Current Phase — 2026-08-29

`v0.1.0` is the current formal release. `main` contains additional unreleased pre-stable work.

The bounded A2A profile has advanced beyond synchronous `SendMessage`: common A2A Task projection plus Task retrieval/cancellation are implemented. Protocol baseline auditing/drift protection is also implemented. The next active work is shared security integration, deployment-declared A2A skills, waiting/resume/async semantics, and then streaming/resubscription.

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
security policy primitives
```

Engine-native state never becomes the public API contract.

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

Standard ADK/LangGraph images remain roughly 266 MB / 248 MB decimal after the 2026-08-27 dependency refresh and avoid the multi-gigabyte Torch/CUDA path.

## Verified Kubernetes Sandbox Acceptance — 2026-08-28

Kubernetes real-cluster acceptance is green on kind with Kubernetes 1.37 and Calico NetworkPolicy enforcement.

Verified behavior includes end-to-end `run.container`, numeric non-root execution, timeout/cancellation cleanup, secret-safe injection, namespace-bounded controller RBAC, and default-deny workload networking.

OpenShift-specific SCC/security-context/arbitrary-UID acceptance remains deferred until an OpenShift cluster is available.

## Bounded Lifecycle Streaming

Common lifecycle SSE is implemented at:

```text
GET /v1/events/lifecycle/stream
```

It is a bounded engine-neutral observation stream over common lifecycle CloudEvents. It is not token/output streaming and is not itself an A2A binding.

Implemented guarantees include bounded replay with `Last-Event-ID`, bounded queues/subscriber count, byte/event/lifetime limits, sanitized lifecycle payloads, and no engine-native checkpoint/stream exposure.

## Protocol Baselines

Pinned reviewed baselines are machine-readable in `resources/protocol-baselines.yaml`:

| Protocol/specification | Baseline | Current position |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | implemented Portable Profile subset |
| A2A Protocol | release `1.0.1`, protocol `1.0` | bounded v1 SendMessage + Task get/cancel profile |
| Model Context Protocol | `2026-07-28` | bounded common client/tool profile |
| OpenAPI Specification | `3.2.0` | bounded operation adapter; no full parser/conformance claim |
| CloudEvents | `1.0.2` | bounded lifecycle events using `specversion: 1.0` |
| AsyncAPI | `3.1.0` | future binding baseline; not implemented |

A deterministic root test guards baseline drift across the manifest, runtime constants, supported bounded method sets, and documentation. Protocol versions never float automatically at runtime.

### Open Workflow vs A2A method vocabulary

Open Workflow 1.0.3 defines its own A2A call method vocabulary such as `message/send`, `tasks/get`, and `tasks/cancel`. OWA preserves the official Open Workflow schema unchanged and translates those schema-defined call values inside `RuntimeServices.call_protocol()` to the official A2A v1 wire operations (`SendMessage`, `GetTask`, `CancelTask`, etc.).

Therefore:

- workflow DSL method names are not external A2A compatibility aliases;
- the external A2A JSON-RPC endpoint accepts the official v1 operation names only;
- A2A v0.3 wire aliases remain intentionally unsupported.

## Current A2A State

Source of truth for wire behavior: the official A2A Project specification/definitions at `a2a-protocol.org`.

Pinned baseline:

```text
A2A release:   1.0.1
protocol:      1.0
```

The bounded inbound A2A profile is implemented, disabled by default, and deployment-controlled.

Implemented endpoints/operations:

```text
GET  /.well-known/agent-card.json

JSON-RPC at <configured A2A path>
  SendMessage
  GetTask
  CancelTask

HTTP+JSON
  POST <configured A2A path>/message:send
  GET  <configured A2A path>/tasks/{id}
  POST <configured A2A path>/tasks/{id}:cancel
```

Implemented Task model:

```text
A2A Task id        = OWA invocation_id
A2A contextId      = OWA session_id
Task state         = projection of common invocation status
Task artifacts     = sanitized common invocation output
engine references  = never exposed
```

Current state mapping:

| OWA invocation | A2A Task state |
| --- | --- |
| `running` | `TASK_STATE_WORKING` |
| `waiting` | `TASK_STATE_INPUT_REQUIRED` |
| `completed` | `TASK_STATE_COMPLETED` |
| `faulted` | `TASK_STATE_FAILED` |
| `cancelled` | `TASK_STATE_CANCELED` |

Task output projection follows official v1 Part JSON shapes: text uses `text`, structured JSON uses `data`, and byte output uses base64 `raw`. Waiting/failure status messages include the Task/context identifiers and only sanitized common error information.

Official bounded Task error mappings implemented for JSON-RPC:

```text
Task not found       -32001
Task not cancelable  -32002
```

HTTP+JSON uses the matching official-style 404/400 boundary for the same conditions.

`features.a2a` currently advertises Tasks with exactly `GetTask` and `CancelTask`; streaming and push notifications remain false.

Remote verification for the earlier SendMessage-only A2A slice at commit `a20ef51` remains:

| Workflow | Run | Result |
| --- | --- | --- |
| CI | `33156173321` | green |
| External Sandbox | `33156173327` | green |
| PostgreSQL | `33156173308` | green |
| Release/latest refresh | `33156335003` | success |

The newer Task profile is unreleased `main` work and is being verified by the normal CI matrix before being recorded as a release.

## Remaining A2A Work

The remaining order is:

```text
shared named security RuntimeConfig/adapters
  -> deployment-declared skills + per-skill authorization
  -> waiting/input-required + resume mapping
  -> SendMessageConfiguration.returnImmediately async behavior
  -> streaming/resubscription over common lifecycle events
  -> external interoperability/conformance evidence
```

The official A2A v1 semantics are the guide: ordinary `SendMessage` blocks by default, while `returnImmediately=true` is the protocol-native non-blocking request and returns Task state for later `GetTask`/subscription. OWA will not add a custom async flag.

Push notifications remain separately deferred because they create an outbound callback trust boundary requiring allowlisting, TLS identity verification, callback authentication, SSRF protection, replay/idempotency controls, bounded retries/dead-letter handling, and secret-safe observability.

Delegated-user identity, token exchange, and consent remain deployment/identity-platform concerns and do not block Task support.

## Security Architecture State

Framework-neutral security primitives are now implemented and deterministically tested for:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Implemented security groundwork:

- named profile model with discriminated profile types;
- secrets represented as deployment environment references, not inline values;
- secret resolution only at use time;
- validation errors hide rejected input values to prevent accidental secret echo;
- OAuth2 client-credential token endpoint requires HTTPS;
- principal/identity, roles, scopes, actions, resources, and audience remain distinct;
- deterministic authorization policy evaluation exists in core;
- `RuntimeConfig.security.profiles` is a strict-parsed section of the main runtime configuration, with `OWA__SECURITY__...` overrides;
- A2A inbound bearer authentication resolves a named `bearer` security profile (`a2a.security_profile`), replacing the temporary `auth_token` field; `RuntimeConfig` rejects unknown or non-bearer profile references at startup; a missing deployment secret at request time fails closed (401), not a crash.

Still active:

- wire named profiles into outbound protocol adapters (MCP/HTTP/OpenAPI clients, external catalog fetches) and the approvals operator check (`ApprovalConfig.operator_token` remains a separate temporary field);
- advertise A2A `securitySchemes` / security requirements accurately;
- enforce per-skill/per-action A2A authorization (depends on deployment-declared A2A skills, `A2A-3`).

OWA does not become an identity provider. OAuth2/OIDC federation, delegated-user token exchange, and consent remain external identity-platform responsibilities.

Traffic/rate/concurrency policy remains a separate deployment concern from authentication/authorization.

## Persistence and State Boundaries

The runtime preserves distinct lifecycles for knowledge, memory, session, common invocation metadata, approvals, schedules, sandbox executions, and engine-native checkpoints/state.

SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented with isolated namespaces. Engine-native checkpoint state is never exposed as a public resume or A2A Task contract.

## Current Active Backlog

The authoritative ordered backlog is `TODO.md`. Current priorities are:

1. finish shared security RuntimeConfig/adapter integration;
2. implement deployment-declared A2A skills and per-skill/action authorization;
3. implement waiting/input-required/resume plus official `returnImmediately` semantics;
4. implement A2A streaming/resubscription only after those Task semantics are stable;
5. add external interoperability/conformance evidence before broadening claims;
6. keep traffic policy separate from security policy.

## Intentionally Deferred

- OpenShift-specific sandbox acceptance until an OpenShift cluster is available.
- A2A push notifications.
- Broad/full A2A conformance claim until async/streaming/interoperability gates are green.
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
