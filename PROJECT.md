# Project Context

## Source of Truth

- `Project Definition.md` — architecture/product contract.
- `PROJECT.md` — verified implementation and release state.
- `TODO.md` — active and intentionally deferred backlog.
- `AGENTS.md` — mandatory repository/contributor rules.

## Current Phase — 2026-09-11

`v0.1.0` is the current formal release. `main` contains additional unreleased pre-stable work.

The bounded inbound A2A profile is complete end to end: common Task projection with get/cancel, deployment-declared skills, per-principal authorization (`a2a.authorization`), waiting→`input-required` mapping, protocol-native `returnImmediately` async behavior, resuming sends over the common resume contract, and bounded streaming/resubscription (`SendStreamingMessage`/`SubscribeToTask`) translating common lifecycle events into official status/artifact frames. Shared security profiles are wired across all inbound/outbound adapters and every temporary credential field is removed. The deployment-controlled `traffic_policy` model is implemented with global token-bucket/concurrency limits plus endpoint-prefix and authenticated-principal scopes. External interoperability/conformance evidence is complete for all advertised baselines.

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
  SendStreamingMessage
  SubscribeToTask

HTTP+JSON
  POST <configured A2A path>/message:send
  GET  <configured A2A path>/tasks/{id}
  POST <configured A2A path>/tasks/{id}:cancel
  POST <configured A2A path>/message:stream
  POST <configured A2A path>/tasks/{id}:subscribe
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

`SendMessage` follows official async semantics: blocking sends return `result.message` on completion or `result.task` when the workflow ends up waiting (`TASK_STATE_INPUT_REQUIRED`); `configuration.returnImmediately: true` starts the invocation and returns the Task projection immediately for `GetTask` polling; sends carrying `message.taskId` resume a waiting task through the common resume contract (fingerprint-verified), while unknown or non-waiting tasks are rejected with sanitized errors.

Streaming/resubscription (`SendStreamingMessage` over `message:stream`, `SubscribeToTask` over `tasks/{id}:subscribe`) streams official `Task`/`statusUpdate`/`artifactUpdate` frames translated from common lifecycle CloudEvents. Streams are bounded by event/byte/duration limits with fail-closed backpressure, disconnecting never cancels the invocation, and engine-native checkpoint/stream objects are never exposed.

`features.a2a` currently advertises Tasks with exactly `GetTask` and `CancelTask`, `SendStreamingMessage`/`SubscribeToTask` streaming operations, the deployment-declared skill ids, bearer authentication, and whether per-principal authorization enforcement is active; push notifications remain false.

Agent Card now advertises `securitySchemes` and `security` requirements when authentication is configured (bearer scheme), conforming to A2A v1 Agent Card specification patterns.

## Remaining A2A Work

The bounded inbound protocol profile — Task state, authorization, skills, async semantics, and streaming/resubscription — is complete (see Security Architecture State and Current A2A State). Remaining active work is listed in `TODO.md`.

The official A2A v1 semantics are the guide: ordinary `SendMessage` blocks by default, while `returnImmediately=true` is the protocol-native non-blocking request and returns Task state for later `GetTask`/subscription. OWA will not add a custom async flag.

Push notifications remain separately deferred because they create an outbound callback trust boundary requiring allowlisting, TLS identity verification, callback authentication, SSRF protection, replay/idempotency controls, bounded retries/dead-letter handling, and secret-safe observability.

Delegated-user identity, token exchange, and consent remain deployment/identity-platform concerns and do not block Task support.

Interoperability evidence is now provided through capability-accuracy tests, A2A v1 spec conformance tests, security scheme advertisement, and multi-operation consistency tests.

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
- A2A inbound bearer authentication resolves a named `bearer` security profile (`a2a.security_profile`), replacing the temporary `auth_token` field; `RuntimeConfig` rejects unknown or non-bearer profile references at startup; a missing deployment secret at request time fails closed (401), not a crash;
- the approvals operator check (`approvals.operator_security_profile`), external-catalog authentication (`authentication.security_profile`), per-tool authentication (`tools[].security_profile`), and workflow-initiated outbound protocol calls (`protocols.security_profile`) all resolve named `bearer`/`api_key` profiles fail-closed at call time;
- A2A per-principal authorization (`a2a.authorization`) enforces explicit allow rules — `message.send` on `skill:<id>` (or `skill:workflow` for the implicit skill), `tasks.get`/`tasks.cancel` on the `tasks` collection — against the authenticated profile principal; first matching rule allows, no match returns a sanitized 403, and a policy declared without a security profile is rejected at startup;
- every temporary credential field is removed (`a2a.auth_token`, `approvals.operator_token`, external-catalog `bearer_token_env`/`basic_*_env`, ambient `OWA_BEARER_TOKEN_ENV`/`OWA_BASIC_*` protocol-client variables); credentials resolve exclusively through named profiles;
- secret-safety verification tests assert the resolved token value never appears on Agent Cards, capability documents, A2A Task projections, protocol error bodies, or configuration validation errors.

Still active:

- broader identity-platform integration remains outside OWA; OAuth2 client-credentials and mTLS profile types are wired to the outbound protocol adapter and covered by core tests.

OWA does not become an identity provider. OAuth2/OIDC federation, delegated-user token exchange, and consent remain external identity-platform responsibilities.

Traffic/rate/concurrency policy remains a separate deployment concern from authentication/authorization.

## Traffic Policy State

The deployment-controlled `traffic_policy` model is implemented:

```text
TrafficPolicyConfig
  enabled: bool = false
  rate_limit: RateLimitConfig (token bucket, requests_per_second + burst)
  concurrency_limit: ConcurrencyLimitConfig (max_concurrent)
  endpoint_limits: list[TrafficEndpointLimitConfig] (longest path-prefix scope)
  principal_limits: list[TrafficPrincipalLimitConfig] (authenticated identity scopes)
```

Implemented behavior:

- token bucket rate limiting with configurable requests-per-second and burst capacity;
- concurrent request limiting with configurable max concurrent requests;
- additional longest-matching endpoint-prefix limits and authenticated-principal limits;
- static bearer/API-key profile identities, including the bounded A2A profile, are attached to the request scope before traffic admission;
- 429 responses with structured error codes (`rate_limit_exceeded`, `concurrency_limit_exceeded`);
- ASGI middleware applied only when `traffic_policy.enabled=true`;
- capability advertisement at `/v1/capabilities` under `features.trafficPolicy`;
- full configuration via YAML or `OWA__TRAFFIC_POLICY__*` environment overrides.

Traffic policy is intentionally separate from security profiles — authentication/authorization profiles must not own traffic management.

## Persistence and State Boundaries

The runtime preserves distinct lifecycles for knowledge, memory, session, common invocation metadata, approvals, schedules, sandbox executions, and engine-native checkpoints/state.

SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented with isolated namespaces. Engine-native checkpoint state is never exposed as a public resume or A2A Task contract.

## Verified Follow-up Work — 2026-09-11

The following backlog items are implemented and verified in the current worktree:

- `SECURITY-8`: security response headers are configurable under `server.security_headers`; HSTS is emitted only for HTTPS ASGI requests.
- `DEPS-1`: `numpy` and `pypdf` are optional knowledge dependencies in both root and standalone-core package metadata, with deferred imports.
- `FIX-1`: the default FastEmbed provider is lazy with respect to optional NumPy, so minimal/core-only and isolated engine environments can construct `RuntimeServices`; knowledge operations fail with a bounded error when the extra is absent.
- `FIX-2`: knowledge manifest reuse checks include file hash, parser identity, chunking identity, and embedding identity, so parser/chunking changes trigger deterministic re-indexing.
- `SEC-1` and `SEC-2`: deployment-owned authentication and operation/idempotency headers override workflow/tool payload values; protocol endpoints reject embedded credentials; A2A public-base URLs reject malformed ports, credentials, queries, and fragments; protocol allowlist host matching is normalized.
- `VALIDATE-1`: non-positive protocol timeouts are rejected before request dispatch.
- `DEPS-4`: built-in `agent:1.0.0` and `llm:1.0.0` catalog manifests declare valid input/output JSON Schemas.
- `DOCS-6` and `DOCS-8`: contributor architecture and bounded A2A streaming documentation are current.
- `K8S-3`: runtime-namespace default-deny NetworkPolicy is provided and covered by manifest tests.
- `DEPLOY-2`: the protected release workflow publishes runtime and sandbox-controller images for `linux/amd64` and `linux/arm64`, with a workflow regression test.
- `DEPLOY-1` progress: the Kubernetes/OpenShift controller now leaves the workload UID unset for `platform=openshift` so restricted SCC can inject the project UID range, while retaining the fixed non-root UID for vanilla Kubernetes. The OpenShift acceptance harness checks SCC assignment, arbitrary-UID execution, security context, RBAC, network denial, workspace writes, and cleanup; real-cluster execution remains pending.
- `OBS-1`: `/metrics` exposes bounded Prometheus text metrics for workflow lifecycle, task and sandbox events, HTTP/A2A traffic, traffic policy, scheduler jobs, and pending approvals.
- `DEPS-3`: strict mypy checks cover core, ADK, LangGraph, and optional Agent Framework adapter packages, with native SDK boundaries explicitly isolated.
- `DOCS-1` through `DOCS-4`: the API guide now documents memory tools, scheduling, approvals, generic events, lifecycle snapshots, and bounded SSE replay.
- `DOCS-5`: the default-profile OpenAPI schema is versioned at `docs/openapi.json` and compared against the generated FastAPI schema in CI tests.
- `DOCS-7`: custom catalog function authoring, manifest layout, protocol boundaries, trust policy, and verification are documented in `docs/custom-catalog-functions.md`.
- `DOCS-9`: deployment configuration recipes cover A2A, security profiles, traffic policy, sandbox backends, protocol tools, and PostgreSQL persistence.
- `DOCS-1` follow-up: the architecture and engine README wording now describes the internal sandbox as a controlled child-process boundary and the production adapters as native execution envelopes; task-level native compilation is not claimed.
- `CI-1a`: documentation-only changes have a lightweight Markdown relative-link workflow, while the full code/test workflow remains path-scoped.
- `TEST-2`: dependency-free benchmark harness reports compilation latency, sequential invocation latency, and concurrent throughput as JSON.
- `TEST-3`: core coverage is enforced at 90%; the full suite currently reports 632 passed, 8 skipped, and 91.09% exact coverage (565/6341 statements missed) with expanded deterministic tests across protocol, catalog, storage, knowledge, lifecycle, sandbox, API-boundary, scheduling, tool, and server paths.
- `TEST-4`: Linux/WSL-compatible mutmut coverage targets the framework-neutral traffic-policy middleware with 13 direct tests; the current 316-mutant baseline kills 272 mutants, records 22 survivors, 18 timeouts, and 4 mutants without test association for future test-strengthening work.
- `TEST-5`: deterministic 100-request async stress coverage verifies traffic-policy concurrency bounds and burst admission without external services.
- `TEST-1` progress: the portable CTK subset now covers 22 feature files, 42 scenarios, and 84 deterministic executions across both available engines, including pinned upstream data-flow filtering, HTTP and OpenAPI content/response output projections, caught and uncaught protocol errors, alongside successful and failing HTTP, MCP, A2A, OpenAPI, event, catalog-call, registered child-workflow `run`, input rejection, flow, policy, transform, validation, nested-input, sequence, and retry scenarios over shared common services; broader upstream CTK coverage and additional implemented features remain open.
- `K8S-2`: optional Kubernetes Ingress and Gateway API `HTTPRoute` templates target the reference runtime Service; deployment-owned host, TLS, ingress-class, and Gateway-parent values remain explicit placeholders.
- `K8S-4`: optional Prometheus Operator `ServiceMonitor` and `PrometheusRule` templates scrape `/metrics` and alert on HTTP error rate, p95 latency, and sandbox failures; CRD fields are covered by targeted manifest tests.
- `K8S-1`: reusable Helm chart under `deploy/helm/open-workflow-agent` packages the runtime Deployment, Service, PVC, default-deny network policy, and opt-in edge/monitoring integrations; `helm lint` and rendered integration tests pass.
- `ARCH-3`: traffic policy supports additional longest-matching endpoint-prefix and authenticated-principal rate/concurrency scopes, with static HTTP/A2A security profile identities propagated before admission and capability/configuration/enforcement tests.

The relevant core tests, package builds, lock checks, repository-wide Ruff, formatting, and mypy checks passed. The full core suite currently passes with 632 passed, 8 skipped, and 91.09% exact coverage. The locked ADK and LangGraph matrices each pass with 168 tests; the optional Agent Framework matrix passes with 240 tests. The documentation relative-link validator also passes.

## Current Active Backlog

The authoritative ordered backlog is `TODO.md`. Current priorities are:

1. OpenShift sandbox acceptance;
2. repository branch-protection configuration for documentation-only changes;
3. review of the five open Dependabot updates;
4. CTK expansion while maintaining the 90% core coverage gate;
5. native-engine depth and differentiated benchmark evidence.

## Intentionally Deferred

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
uv sync --locked --extra knowledge
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy --package open_workflow_agent --package open_workflow_agent_adk --package open_workflow_agent_langgraph --package open_workflow_agent_agent_framework
uv run --locked pytest -q --cov=core/src/open_workflow_agent --cov-fail-under=90
uv build
uv build --directory core
uv run --directory engines/adk --locked --extra native --extra knowledge --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
uv run --directory engines/langgraph --locked --extra sqlite --extra knowledge --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
uv run --directory engines/agent-framework --locked --extra native --extra knowledge --with pytest --with pytest-asyncio pytest ../../tests/agent_framework ../../tests/contract ../../tests/ctk -q
```
