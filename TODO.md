# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**v0.1.0 released; latest-stable protocol migration and shared security-model implementation are the active pre-stable work (2026-08-28).**

The runtime has no backward-compatibility commitment while the public product contract is still stabilizing. Legacy protocol aliases should be removed during migrations rather than preserved automatically.

### Current implementation state

- Stable protocol baselines are pinned in `docs/protocol-baselines.md`.
- Inbound A2A is migrated from the legacy v0.3 assumptions to the stable A2A 1.0 line (`1.0.1` release, protocol version `1.0`).
- The common outbound MCP/A2A client migration is on `main`; final protocol-wide verification remains part of `PROTOCOL-2/3`.
- The sandbox capability SPI rename is complete: `sandbox_capabilities.py` is separate from backend `sandbox/contract.py`.
- Shared named security profiles are **designed/documented but not yet implemented**; temporary protocol-specific credential fields are not future compatibility contracts.
- The formal release remains **v0.1.0**; current `main` work is unreleased.

## Active Backlog

### Protocol baselines

- [x] PROTOCOL-1: inventory external protocols/specifications and verify latest stable released baselines from authoritative sources.
  - Open Workflow Specification: `1.0.3`
  - A2A Protocol: `1.0.1`
  - Model Context Protocol: `2026-07-28`
  - OpenAPI Specification: `3.2.0`
  - CloudEvents: `1.0.2`
  - AsyncAPI Specification: `3.1.0`
  - gRPC: no independent OWA application-protocol version is pinned unless a concrete OWA binding requires one.
- [ ] PROTOCOL-2: finish auditing/migrating implemented protocol behavior to the pinned stable baselines. A2A migration is complete; MCP common-client migration is on `main`; OpenAPI remains a bounded operation adapter rather than a full OAS 3.2 parser/conformance claim; CloudEvents structured lifecycle behavior remains bounded to the supported 1.0 semantics.
- [ ] PROTOCOL-3: add deterministic compatibility/conformance tests for every pinned baseline and advertise protocol/version only after applicable gates are green.
- [x] PROTOCOL-4: no backward-compatibility commitment before product-contract stabilization. A2A v0.3 compatibility was removed during the v1 migration.
- [ ] PROTOCOL-5: add a release/CI guard preventing an unreviewed protocol-baseline change from being advertised as supported.

### Security configuration model

- [ ] SECURITY-1: introduce shared named security profiles for inbound and outbound protocol adapters. Initial types: `bearer`, `api_key`, `oauth2_client_credentials`, `mtls` only.
- [ ] SECURITY-2: expose profiles through strict YAML plus `OWA__...` overrides. Protocol/tool configuration may reference profiles; workflow definitions must never contain raw credentials.
- [ ] SECURITY-3: resolve sensitive values from deployment secret/environment references and keep secrets out of logs, plans, capability responses, Agent Cards, lifecycle events, Tasks/artifacts, sandbox output, and persisted invocation metadata.
- [ ] SECURITY-4: implement standard authorization vocabulary: **principal/identity**, **role**, **scope**, **permission/action**, **resource**, **audience**. Keep roles and scopes semantically distinct. Use protocol-native action identifiers where applicable (`message.send`, `tasks.get`, `tasks.cancel`).
- [ ] SECURITY-5: keep enterprise OAuth2/OIDC federation, token exchange, delegated-user identity, and consent outside OWA. Delegated-user support stays deferred until a concrete requirement exists.
- [ ] SECURITY-6: remove temporary protocol-specific authentication fields as shared profiles replace them. Do not retain aliases merely for backward compatibility.

### Traffic policy

- [ ] TRAFFIC-1: introduce a separate deployment-controlled `traffic_policy` model for rate limits, concurrency limits, burst/admission control, and future circuit policies. Authentication/authorization profiles must not own traffic management.

### A2A next bounded profile

- [x] A2A-1: migrate Agent Card/discovery/transport metadata and bounded `SendMessage` behavior to A2A `1.0.1`; advertise protocol version `1.0`, use `/.well-known/agent-card.json`, `supportedInterfaces`, JSON-RPC `SendMessage`, HTTP+JSON `/message:send`, v1 Part shape, and no v0.3 compatibility aliases.
- [ ] A2A-2: replace the temporary shared bearer-only model with named security profiles and per-principal skill/action authorization.
- [ ] A2A-3: support multiple config-declared A2A skills mapped to explicitly registered workflows. Clients must not choose arbitrary workflow paths/files/catalog entries.
- [ ] A2A-4: implement A2A Tasks as a projection over common OWA invocation state, not a second execution engine. Prefer `task_id == invocation_id`; validate exact TaskStatus mapping against A2A `1.0.1`.
- [ ] A2A-5: add Task retrieval and cancellation using common invocation/ExecutionHandle APIs. Durable waiting/approval state stays owned by existing common stores.
- [ ] A2A-6: add asynchronous Task-returning send behavior only as defined by A2A `1.0.1`; no OWA-specific async flag.
- [ ] A2A-7: after Task state is green, implement streaming/resubscription using the bounded common lifecycle/event infrastructure. Never expose engine-native checkpoint/stream objects.
- [ ] A2A-8: add A2A interoperability/conformance coverage before expanding capability advertisement.

### Architecture cleanup

- [x] ARCH-3: portable sandbox requirements/capability SPI renamed to `sandbox_capabilities.py`; backend request/result/interface contract remains `sandbox/contract.py`. The concepts stay separate.

## Intentionally Deferred

### OpenShift sandbox acceptance

Kubernetes acceptance is green. OpenShift-specific SCC/security-context/arbitrary-UID validation remains deferred until an OpenShift cluster is available. Do not advertise OpenShift-specific enforcement until that acceptance runs.

### A2A push notifications and full conformance

Push notifications remain deferred because they create an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

A full A2A conformance claim remains deferred until the bounded Task/streaming profile and applicable interoperability/conformance gates are complete.

### Microsoft Agent Framework production status

The optional adapter remains CI-covered but is not a production image/release target. Independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata remain deferred.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid preventing future tenant isolation, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation/token exchange/consent is deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

## Decisions Resolved — 2026-08-28

- [x] Latest stable released protocol/specification is the target baseline; baselines are explicitly pinned and reviewed.
- [x] No backward compatibility is required at this stage.
- [x] Security configuration is externalized through YAML/environment variables and reusable named profiles.
- [x] Initial security mechanisms are limited to bearer, API key, OAuth2 client credentials, and mTLS.
- [x] Authorization vocabulary uses standard principal/identity, role, scope, permission/action, resource, and audience terms.
- [x] Traffic/rate/concurrency policy is separate from authentication/authorization.
- [x] A2A supports multiple deployment-configured skills mapped to registered workflows as the target model.
- [x] A2A Tasks project common OWA invocation state as the target model.
- [x] Multi-tenancy remains out of scope.
- [x] Delegated-user identity remains deferred.
- [x] Sandbox capability SPI and backend contract remain separate; the capability module rename is complete.

Detailed decisions: `docs/protocol-security-decisions.md`.
Protocol baseline record: `docs/protocol-baselines.md`.

## Working Rules

- Add/update tests before marking implementation tasks complete.
- Keep core framework-neutral; engine packages own framework-specific behavior only.
- Route executable workflow operations through common `SandboxManager`.
- Preserve separate knowledge, memory, session, checkpoint, invocation, approval, schedule, sandbox, and engine-native state lifecycles.
- Production capabilities remain fail-closed until deterministic tests and required acceptance gates are green.
- Protocol baseline changes are reviewed compatibility changes, not dependency bumps.
- Authentication and authorization remain deployment/runtime configuration; workflows never contain raw credentials.
- Traffic policy remains separate from security profiles.
