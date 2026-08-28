# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**v0.1.0 released; protocol/security decisions resolved; latest-stable protocol migration and security-model implementation in progress (2026-08-28).**

The full backlog sweep is green, v0.1.0 is released, Kubernetes sandbox acceptance passed on kind, and inbound A2A has now been migrated from the legacy v0.3 assumptions to the stable A2A 1.0 line. The next work is no longer decision-blocked.

### Current integration head

- A2A v1 code verification: CI run `33171216121` is green for commit `cb529e1` (root quality/tests/contracts, ADK/LangGraph native+CTK, and Docker acceptance).
- The A2A v1 migration is documented in README/API plus the protocol baseline/security decision records; subsequent documentation-only commits do not change runtime behavior.
- The current formal release remains **v0.1.0** (`c47cb86`); current `main` work is unreleased.

## Active Backlog

### Protocol baselines

- [x] PROTOCOL-1: inventory the external protocols/specifications implemented or planned by OWA and verify the latest stable released baseline from authoritative sources. Recorded in `docs/protocol-baselines.md`.
  - Open Workflow Specification: `1.0.3`
  - A2A Protocol: `1.0.1`
  - Model Context Protocol: `2026-07-28`
  - OpenAPI Specification: `3.2.0`
  - CloudEvents: `1.0.2`
  - AsyncAPI Specification: `3.1.0`
  - gRPC: no independent OWA application-protocol version is pinned; use the stable protocol/tooling required by the binding being implemented.
- [ ] PROTOCOL-2: update existing implementations that lag the verified stable baseline. A2A migration is complete; audit/migrate existing MCP, OpenAPI, and CloudEvents behavior next where required.
- [ ] PROTOCOL-3: add deterministic compatibility/conformance tests for every pinned baseline and advertise protocol/version only after the applicable gates are green.
- [x] PROTOCOL-4: **no backward-compatibility commitment before the product contract stabilizes.** Remove legacy protocol aliases/behavior during migrations unless explicitly required later by a new product decision. A2A v0.3 compatibility was removed during the v1 migration.
- [ ] PROTOCOL-5: add a release/CI check preventing an unreviewed protocol-baseline change from being advertised as supported.

### Security configuration model

- [ ] SECURITY-1: introduce shared named security profiles used by inbound and outbound protocol adapters. Initial profile types are intentionally limited to the most common interoperable mechanisms: `bearer`, `api_key`, `oauth2_client_credentials`, and `mtls`. Do not build uncommon/legacy mechanisms without a concrete requirement.
- [ ] SECURITY-2: expose security configuration through strict YAML plus `OWA__...` environment-variable overrides. Workflow definitions/tools may reference configured profiles but must not contain raw credentials.
- [ ] SECURITY-3: support environment/secret references for sensitive values and keep secrets out of logs, plans, capability responses, Agent Cards, lifecycle events, tasks/artifacts, and persisted invocation metadata.
- [ ] SECURITY-4: use standard authorization vocabulary in configuration and documentation: **principal/identity**, **role**, **scope**, **permission/action**, **resource**, and **audience**. Protocol-specific action identifiers should follow their protocol naming where available, for example `message.send`, `tasks.get`, and `tasks.cancel`.
- [ ] SECURITY-5: enterprise OAuth2/OIDC federation, token exchange, delegated-user identity, and consent remain deployment/identity-platform concerns. OWA must not become an identity provider; delegated-user support is deferred until a concrete use case exists.

### Traffic policy

- [ ] TRAFFIC-1: introduce a separate deployment-controlled `traffic_policy` configuration model for inbound rate limits, concurrency limits, request/body bounds where not already protocol-specific, and future circuit/burst policies. Authentication/authorization profiles must not own traffic management.

### A2A next bounded profile

- [x] A2A-1: migrated Agent Card/discovery/transport metadata and bounded SendMessage behavior to stable A2A `1.0.1`; runtime advertises protocol version `1.0`, uses `/.well-known/agent-card.json`, `supportedInterfaces`, JSON-RPC `SendMessage`, HTTP+JSON `/message:send`, v1 Part shape, and no v0.3 compatibility aliases. CI run `33171216121` green.
- [ ] A2A-2: replace the single shared bearer-only model with named security profiles and per-principal skill/action authorization.
- [ ] A2A-3: support multiple config-declared A2A skills mapped to explicitly registered workflows. Clients must not choose arbitrary workflow paths/files/catalog entries.
- [ ] A2A-4: implement the A2A Task model as a projection over common OWA invocation state, not a second execution engine. Prefer `task_id == invocation_id`; validate exact TaskStatus mapping against A2A `1.0.1`.
- [ ] A2A-5: add Task retrieval and cancellation using common invocation/ExecutionHandle APIs. Durable waiting/approval state stays owned by existing common stores.
- [ ] A2A-6: add asynchronous Task-returning send behavior only as defined by A2A `1.0.1`; no OWA-specific async flag.
- [ ] A2A-7: after the Task model is green, implement streaming/resubscription by mapping A2A events onto the existing bounded common lifecycle/event infrastructure. Never expose engine-native checkpoint/stream objects.
- [ ] A2A-8: add A2A interoperability/conformance coverage for the bounded profile before expanding capability advertisement.

### Architecture cleanup

- [ ] ARCH-3: rename portable sandbox requirements module `sandbox_contract.py` to `sandbox_capabilities.py` (or equivalent capability-oriented name). Keep `sandbox/contract.py` as the backend request/result/interface contract; do not merge the concepts.

## Intentionally Deferred

### OpenShift sandbox acceptance

Kubernetes acceptance is green. OpenShift-specific SCC/security-context/arbitrary-UID validation remains deferred until an OpenShift cluster is available. Do not advertise OpenShift-specific enforcement until that acceptance runs.

### A2A push notifications and full conformance

Push notifications remain deferred because they introduce an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

A full A2A conformance claim remains deferred until the bounded Task/streaming profile and applicable interoperability/conformance gates are complete.

### Microsoft Agent Framework production status

The optional adapter remains CI-covered but is not a production image/release target. Independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata remain deferred.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid preventing future tenant isolation, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation/token exchange/consent is deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

## Decisions Resolved — 2026-08-28

- [x] Latest stable released protocol/specification is the target baseline; baselines are explicitly pinned and reviewed.
- [x] No backward compatibility is required at this stage; the product contract is not yet mature enough to justify carrying legacy protocol generations.
- [x] Security configuration is externalized through YAML/environment variables and reusable named profiles.
- [x] Initial security mechanisms are limited to bearer token, API key, OAuth2 client credentials, and mTLS.
- [x] Authorization vocabulary uses standard identity/role/scope/permission(action)/resource/audience terms.
- [x] Traffic/rate/concurrency policy is separate from authentication/authorization.
- [x] A2A supports multiple deployment-configured skills mapped to registered workflows.
- [x] A2A Tasks project common OWA invocation state.
- [x] Multi-tenancy remains out of scope.
- [x] Delegated-user identity remains deferred.
- [x] Sandbox capability SPI and backend contract remain separate; rename the former rather than merging them.

Detailed decisions: `docs/protocol-security-decisions.md`.
Protocol baseline record: `docs/protocol-baselines.md`.

## Working Rules

- Add/update tests before marking implementation tasks complete.
- Keep core framework-neutral; engine packages own framework-specific behavior only.
- Route executable workflow operations through common `SandboxManager`.
- Preserve separate knowledge, memory, session, checkpoint, invocation, approval, schedule, sandbox, and engine-native state lifecycles.
- Production capabilities remain fail-closed until deterministic tests and required acceptance gates are green.
- Protocol baseline changes are reviewed compatibility changes, not dependency bumps.
- Authentication and authorization remain strict deployment/runtime configuration; workflow definitions never contain raw credentials.
- Traffic policy remains separate from security profiles.
