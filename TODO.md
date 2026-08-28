# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains only active or intentionally deferred work.

## Current Phase

**v0.1.0 is released. Current `main` is unreleased pre-stable work focused on protocol-baseline completion, shared security policy, and the next bounded A2A profile.**

The public product contract is still stabilizing. Legacy protocol generations are removed during migrations unless an explicit compatibility decision says otherwise.

## Current Implementation State

- Open Workflow Specification baseline: `1.0.3`.
- A2A baseline: stable release `1.0.1`, protocol version `1.0`.
- Bounded inbound A2A is implemented and disabled by default:
  - Agent Card discovery at `/.well-known/agent-card.json`;
  - JSON-RPC `SendMessage` on the configured A2A endpoint;
  - HTTP+JSON `POST <a2a-path>/message:send`;
  - selectable `jsonrpc` and `http_json` transports;
  - optional temporary bearer guard, request/message bounds, sanitized errors;
  - no A2A v0.3 compatibility aliases.
- Common outbound MCP/A2A protocol clients have been migrated toward the pinned stable baselines and are covered by deterministic tests; protocol-wide verification/advertisement gates remain active work.
- Bounded common lifecycle SSE is implemented and remains separate from A2A streaming.
- Kubernetes sandbox real-cluster acceptance is green; OpenShift-specific SCC/arbitrary-UID acceptance is deferred until an OpenShift cluster is available.
- Shared named security profiles are designed/documented but not yet implemented.
- Microsoft Agent Framework remains an optional CI-covered adapter, not a production image/release target.
- Formal release remains `v0.1.0`; current `main` changes are unreleased.

## Active Backlog

### P0 — Protocol baseline completion

- [x] **PROTOCOL-1** — inventory external protocols/specifications and pin the latest stable released baselines from authoritative sources.
  - Open Workflow Specification: `1.0.3`
  - A2A Protocol: `1.0.1`
  - Model Context Protocol: `2026-07-28`
  - OpenAPI Specification: `3.2.0`
  - CloudEvents: `1.0.2`
  - AsyncAPI Specification: `3.1.0`
- [ ] **PROTOCOL-2** — finish auditing/migrating every implemented protocol behavior to the pinned baselines.
  - A2A bounded inbound migration: complete.
  - MCP common-client migration: implemented on `main`; complete final audit/verification.
  - OpenAPI: keep as a bounded operation adapter; do not claim full OAS 3.2 parser/conformance.
  - CloudEvents: verify the bounded lifecycle event contract against the supported stable semantics.
- [ ] **PROTOCOL-3** — add deterministic compatibility/interoperability tests for each advertised pinned baseline and advertise version support only after applicable gates are green.
- [x] **PROTOCOL-4** — no backward-compatibility commitment before public-contract stabilization; A2A v0.3 compatibility was intentionally removed.
- [ ] **PROTOCOL-5** — add a CI/release guard preventing an unreviewed protocol-baseline change from being advertised as supported.

### P0 — Shared security configuration

- [ ] **SECURITY-1** — implement reusable named security profiles for inbound/outbound protocol adapters. Initial types only: `bearer`, `api_key`, `oauth2_client_credentials`, `mtls`.
- [ ] **SECURITY-2** — expose profiles through strict YAML plus `OWA__...` overrides. Protocol/tool configuration references profiles; workflow definitions never contain raw credentials.
- [ ] **SECURITY-3** — resolve sensitive values from deployment secret/environment references and keep secrets out of logs, plans, capability responses, Agent Cards, lifecycle events, A2A Tasks/artifacts, sandbox output, and persisted invocation metadata.
- [ ] **SECURITY-4** — implement standard authorization vocabulary and checks: principal/identity, role, scope, permission/action, resource, audience. Keep roles and scopes distinct. Use protocol-native actions where applicable (`message.send`, `tasks.get`, `tasks.cancel`).
- [x] **SECURITY-5** — enterprise OAuth2/OIDC federation, token exchange, delegated-user identity, and consent remain outside OWA. Delegated-user support stays deferred until a concrete requirement exists.
- [ ] **SECURITY-6** — remove temporary protocol-specific credential fields as shared profiles replace them. Do not retain aliases only for backward compatibility.

### P1 — Traffic policy

- [ ] **TRAFFIC-1** — introduce a separate deployment-controlled `traffic_policy` model for rate limits, concurrency limits, burst/admission control, and future circuit policies. Authentication/authorization profiles must not own traffic management.

### P1 — A2A next bounded profile

- [x] **A2A-1** — migrate discovery/transport metadata and bounded `SendMessage` behavior to stable A2A `1.0.1`; advertise protocol version `1.0`; use `/.well-known/agent-card.json`, `supportedInterfaces`, JSON-RPC `SendMessage`, HTTP+JSON `/message:send`, and v1 Part shapes; remove v0.3 aliases.
- [ ] **A2A-2** — replace the temporary bearer-only model with shared named security profiles and per-principal skill/action authorization.
- [ ] **A2A-3** — support multiple deployment-configured A2A skills mapped only to explicitly registered workflows. Clients must never select arbitrary workflow paths/files/catalog entries.
- [ ] **A2A-4** — implement A2A Tasks as a thin projection over common OWA invocation state, not a second execution/persistence engine. Prefer `task_id == invocation_id` unless the pinned protocol requires otherwise. Validate the exact TaskStatus mapping against A2A `1.0.1`.
- [ ] **A2A-5** — add Task retrieval and cancellation through common invocation/`ExecutionHandle` APIs. Durable waiting/approval state remains owned by existing common stores.
- [ ] **A2A-6** — map waiting/input-required/resume behavior to the A2A Task model using protocol-native semantics; do not invent an OWA-specific async flag.
- [ ] **A2A-7** — after Task state is green, implement A2A streaming/resubscription over the common lifecycle/event infrastructure. Never expose engine-native checkpoint or stream objects.
- [ ] **A2A-8** — add A2A interoperability/conformance coverage and capability-accuracy tests before expanding advertisement.

### Recommended implementation order for A2A

```text
shared security profiles
  -> deployment-declared skills
  -> Task projection
  -> tasks/get + tasks/cancel
  -> waiting/input-required + resume mapping
  -> spec-native async behavior
  -> message/task streaming + resubscription
  -> interoperability/conformance gates
```

The current blocker for streaming is therefore **not engine streaming**. It is the missing portable A2A Task/message/artifact lifecycle projection and its authorization/capability contract.

## Intentionally Deferred

### OpenShift sandbox acceptance

Kubernetes acceptance is green. OpenShift-specific SCC/security-context/arbitrary-UID validation remains deferred until an OpenShift cluster is available. Do not advertise OpenShift-specific enforcement until that acceptance runs.

### A2A push notifications

Push notifications remain deferred because they introduce an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

### Full A2A conformance claim

A broad/full A2A conformance claim remains deferred until the bounded Task/streaming profile and applicable interoperability/conformance gates are complete. Advertise only the implemented bounded profile.

### Microsoft Agent Framework production status

The optional adapter remains CI-covered but is not a production image/release target. Independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata remain deferred.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid obvious future tenant-isolation blockers, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation/token exchange/consent is deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

## Working Rules

- Add/update deterministic tests before marking implementation tasks complete.
- Keep core framework-neutral; engine packages own framework-specific behavior only.
- Route executable workflow operations through common `SandboxManager`.
- Preserve separate knowledge, memory, session, checkpoint, invocation, approval, schedule, sandbox, and engine-native state lifecycles.
- Production capabilities remain fail-closed until required tests/acceptance gates are green.
- Protocol baseline changes are reviewed compatibility changes, not dependency bumps.
- Authentication and authorization are deployment/runtime configuration; workflows never contain raw credentials.
- Traffic policy remains separate from security profiles.
- A2A Tasks are protocol projections over common invocation state, never a second workflow engine.

Detailed decisions: `docs/protocol-security-decisions.md`.
Protocol baselines: `docs/protocol-baselines.md`.
Verified implementation state: `PROJECT.md`.
