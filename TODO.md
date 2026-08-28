# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains only active or intentionally deferred work.

## Current Phase

**v0.1.0 is released. Current `main` is unreleased pre-stable work focused on shared security integration, deployment-declared A2A skills, and the remaining A2A Task/streaming profile.**

The public product contract is still stabilizing. External A2A wire behavior targets the official A2A v1 definitions. Open Workflow 1.0.3 keeps its own schema-defined A2A call vocabulary; OWA translates that vocabulary to the selected A2A wire operation at the runtime protocol boundary rather than changing the Open Workflow schema.

## Current Implementation State

- Open Workflow Specification baseline: `1.0.3`.
- A2A baseline: stable release `1.0.1`, protocol version `1.0`, validated against the official A2A Project definitions at `a2a-protocol.org`.
- Bounded inbound A2A is implemented and disabled by default:
  - Agent Card discovery at `/.well-known/agent-card.json`;
  - JSON-RPC `SendMessage`, `GetTask`, and `CancelTask` on the configured A2A endpoint;
  - HTTP+JSON `POST <a2a-path>/message:send`, `GET <a2a-path>/tasks/{id}`, and `POST <a2a-path>/tasks/{id}:cancel`;
  - A2A Task is a sanitized projection over common OWA invocation/`ExecutionHandle` state;
  - selectable `jsonrpc` and `http_json` transports;
  - optional temporary bearer guard, request/message bounds, sanitized errors;
  - no A2A v0.3 wire compatibility aliases.
- Open Workflow DSL `message/send`, `tasks/get`, `tasks/cancel`, and related schema-defined method names are translated in `RuntimeServices.call_protocol()` to official A2A v1 wire operations; they are not exposed as legacy A2A wire aliases.
- Common outbound MCP/A2A protocol clients are pinned to their reviewed stable baselines and covered by deterministic tests.
- Machine-readable protocol baselines live in `resources/protocol-baselines.yaml`; deterministic tests tie the manifest to runtime constants, documentation, supported method sets, and bounded claims.
- Bounded common lifecycle SSE is implemented and remains separate from A2A streaming.
- Framework-neutral security profile primitives now exist for `bearer`, `api_key`, `oauth2_client_credentials`, and `mtls`, including env-only secret references and authorization vocabulary/checks. RuntimeConfig/protocol integration is still active work.
- Security profile validation hides rejected input values so attempted inline secrets are not echoed by validation errors.
- Kubernetes sandbox real-cluster acceptance is green; OpenShift-specific SCC/arbitrary-UID acceptance is deferred until an OpenShift cluster is available.
- Microsoft Agent Framework remains an optional CI-covered adapter, not a production image/release target.
- Formal release remains `v0.1.0`; current `main` changes are unreleased.

## Active Backlog

### P0 — Protocol baseline completion

- [x] **PROTOCOL-1** — inventory external protocols/specifications and pin the latest stable released baselines from authoritative sources.
  - Open Workflow Specification: `1.0.3`
  - A2A Protocol: `1.0.1` / protocol `1.0`
  - Model Context Protocol: `2026-07-28`
  - OpenAPI Specification: `3.2.0`
  - CloudEvents: `1.0.2`
  - AsyncAPI Specification: `3.1.0`
- [x] **PROTOCOL-2** — audit/migrate implemented protocol behavior to the pinned baselines.
  - A2A bounded inbound/client behavior uses stable v1 wire operations and v1 Part/Task shapes.
  - Open Workflow A2A call vocabulary is translated only at the protocol boundary; the official Open Workflow 1.0.3 schema remains untouched.
  - MCP common-client behavior is pinned to `2026-07-28`.
  - OpenAPI remains a bounded operation adapter; no full OAS 3.2 parser/conformance claim.
  - CloudEvents lifecycle contract remains a bounded `specversion: 1.0` profile against the `1.0.2` release baseline.
- [ ] **PROTOCOL-3** — complete external compatibility/interoperability evidence for every advertised pinned baseline before any broad conformance claim.
  - Deterministic local baseline/shape/advertisement tests: complete.
  - Engine-shared Open Workflow CTK/contract coverage: complete for the portable profile.
  - Broad external A2A/MCP/OpenAPI conformance/interoperability suites: not claimed yet.
- [x] **PROTOCOL-4** — no backward-compatibility commitment before public-contract stabilization; A2A v0.3 wire compatibility was intentionally removed.
- [x] **PROTOCOL-5** — CI/release baseline-drift guard implemented through `resources/protocol-baselines.yaml` plus deterministic root tests; an unreviewed manifest/runtime/docs mismatch fails the normal quality gate.

### P0 — Shared security configuration

- [ ] **SECURITY-1** — integrate reusable named security profiles across inbound/outbound protocol adapters. Initial framework-neutral profile models are implemented and tested for `bearer`, `api_key`, `oauth2_client_credentials`, and `mtls`; adapter wiring remains.
- [ ] **SECURITY-2** — expose profiles through the main strict runtime YAML plus `OWA__...` overrides. Protocol/tool configuration references profiles; workflow definitions never contain raw credentials.
- [ ] **SECURITY-3** — complete secret-safe integration across adapters/logs/plans/capabilities/Agent Cards/lifecycle/A2A Tasks/sandbox/persistence. Env-only `SecretReference` resolution and secret-safe validation errors are implemented; full adapter-path verification remains.
- [ ] **SECURITY-4** — wire standard authorization checks into protocol actions/skills. Framework-neutral principal/role/scope/action/resource/audience policy evaluation is implemented and tested; A2A/MCP enforcement remains.
- [x] **SECURITY-5** — enterprise OAuth2/OIDC federation, token exchange, delegated-user identity, and consent remain outside OWA. Delegated-user support stays deferred until a concrete requirement exists.
- [ ] **SECURITY-6** — remove temporary protocol-specific credential fields as shared profiles replace them. Do not retain aliases only for backward compatibility.

### P1 — Traffic policy

- [ ] **TRAFFIC-1** — introduce a separate deployment-controlled `traffic_policy` model for rate limits, concurrency limits, burst/admission control, and future circuit policies. Authentication/authorization profiles must not own traffic management.

### P1 — A2A next bounded profile

- [x] **A2A-1** — migrate discovery/transport metadata and bounded `SendMessage` behavior to stable A2A `1.0.1`; advertise protocol version `1.0`; use `/.well-known/agent-card.json`, `supportedInterfaces`, JSON-RPC `SendMessage`, HTTP+JSON `/message:send`, and v1 Part shapes; remove v0.3 wire aliases.
- [ ] **A2A-2** — replace the temporary bearer-only model with shared named security profiles and per-principal skill/action authorization.
- [ ] **A2A-3** — support multiple deployment-configured A2A skills mapped only to explicitly registered workflows. Clients must never select arbitrary workflow paths/files/catalog entries.
- [x] **A2A-4** — implement A2A Tasks as a thin projection over common OWA invocation state, not a second execution/persistence engine. `task_id == invocation_id`; Task state, context, status messages, artifacts, and raw-byte base64 JSON encoding are covered by deterministic tests against the official v1 definitions.
- [x] **A2A-5** — add Task retrieval and cancellation through common invocation/`ExecutionHandle` APIs. Implemented JSON-RPC `GetTask`/`CancelTask` and HTTP+JSON `/tasks/{id}` / `/tasks/{id}:cancel`, including official Task-not-found `-32001` and Task-not-cancelable `-32002` JSON-RPC mappings.
- [ ] **A2A-6** — map waiting/input-required/resume and protocol-native asynchronous behavior to the A2A Task model. Follow official `SendMessageConfiguration.returnImmediately`; do not invent an OWA-specific async flag.
- [ ] **A2A-7** — after Task state/authorization are green, implement A2A streaming/resubscription over the common lifecycle/event infrastructure. Never expose engine-native checkpoint or stream objects.
- [ ] **A2A-8** — add external A2A interoperability/conformance evidence and capability-accuracy tests before expanding advertisement beyond the bounded Task profile.

### Recommended implementation order for remaining A2A work

```text
shared security RuntimeConfig/adapters
  -> deployment-declared skills + per-skill authorization
  -> waiting/input-required + resume mapping
  -> SendMessageConfiguration.returnImmediately async behavior
  -> message/task streaming + resubscription
  -> interoperability/conformance gates
```

The Task projection and get/cancel blocker is removed. The current blockers for broader A2A streaming/async advertisement are now **shared authorization + skill routing + portable waiting/resume/async semantics**, not engine-native streaming.

## Intentionally Deferred

### OpenShift sandbox acceptance

Kubernetes acceptance is green. OpenShift-specific SCC/security-context/arbitrary-UID validation remains deferred until an OpenShift cluster is available. Do not advertise OpenShift-specific enforcement until that acceptance runs.

### A2A push notifications

Push notifications remain deferred because they introduce an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

### Full A2A conformance claim

A broad/full A2A conformance claim remains deferred until the bounded async/streaming profile and applicable interoperability/conformance gates are complete. Advertise only the implemented bounded profile.

### Microsoft Agent Framework production status

The optional adapter remains CI-covered but is not a production image/release target. Independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata remain deferred.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid obvious future tenant-isolation blockers, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation/token exchange/consent is deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

## Working Rules

- Use the official A2A Project website/specification definitions as the source of truth for A2A wire behavior.
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
