# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains the scoped backlog and intentionally deferred work.

## Current Phase

**`v0.2.0` is the current formal release. Readiness foundations are complete; activation-gated integration tasks remain pending their product and deployment decisions.**

The public contract remains Open Workflow 1.0.3. The runtime uses a framework-neutral common executor and deployment-selected ADK or LangGraph envelopes; it does not claim task-level native compilation or full Open Workflow/A2A conformance.

## Active backlog

### Readiness foundation and activation gates

This milestone applies safe engineering practices without changing the public
Open Workflow 1.0.3 contract or advertising unsupported runtime behavior. Work
is ordered: complete the foundation tasks first, then activate only the track
whose product and deployment prerequisites are explicit.

#### P0 — Safe foundation (complete)

- [x] **READINESS-1** — record the Agent Framework support-tier decision and activation criteria: it remains an optional CI/evaluation adapter, not a production engine, until users, support expectations, deployment environments, compatibility promise, and rollback ownership are explicitly chosen.
- [x] **READINESS-2** — record the transport-neutral event contract baseline: CloudEvents-shaped identity, schema/version policy, correlation, idempotency, replay, backpressure, redaction, authentication, TLS, and bounded delivery rules.
- [x] **READINESS-3** — cross-link the readiness plans through the backlog, project authority, API/protocol baselines, deployment, troubleshooting, README, engine documentation, and changelog; verify the documentation links and diff.

#### P1 — Agent Framework production activation (gated)

- [ ] **ENGINE-5** — after an explicit production-support decision, audit the isolated dependency graph, license posture, provider exclusions, image size, startup behavior, and package/lock reproducibility. Do not add a production image before the audit is recorded.
- [ ] **ENGINE-6** — implement adapter-owned native checkpoint/resume integration using trusted private storage, stable logical executor identities, restricted serialized types, and no public framework-native state. Preserve the common invocation/resume contract and `SandboxManager` boundary.
- [ ] **ENGINE-7** — add deterministic native tests for execution, cancellation, checkpoint creation, restart, rehydration, resume, stable operation identity, side-effect replay/idempotency, capability reporting, and failure sanitization; run common contract and Portable Profile CTK suites.
- [ ] **ENGINE-8** — build and accept an independent Agent Framework runtime image only after ENGINE-5 through ENGINE-7 are green; pass hardened-image, runtime, vulnerability, SBOM, provenance, multi-platform, rollback, and release-documentation gates before advertising production support.

#### P1 — Concrete event integration and AsyncAPI (gated)

- [ ] **EVENT-1** — capture the first real consumer and use case, selected transport/broker, delivery semantics, ordering, retention, replay, failure policy, security policy, payload limits, operational owner, and tenant/delegated-identity impact. This task is blocked until a concrete integration requirement exists.
- [ ] **EVENT-2** — define the implemented event types and versioned JSON Schemas against the existing CloudEvents envelope, including correlation/causation, redaction, idempotency, compatibility, and loss/retry semantics.
- [ ] **EVENT-3** — implement the selected transport adapter with deployment-owned authentication/TLS/policy, bounded queues and retries, replay/dead-letter handling, metrics, and interoperability tests. Do not add a broker dependency before EVENT-1 selects the transport.
- [ ] **EVENT-4** — author and validate an AsyncAPI 3.1 document describing only implemented channels, operations, messages, schemas, servers, and protocol bindings. AsyncAPI is not a substitute for EVENT-1 or EVENT-3.
- [ ] **EVENT-5** — update capabilities, deployment/API documentation, security review, CI validation, producer/consumer acceptance, rollback references, and release metadata only after EVENT-1 through EVENT-4 are green.

Milestone exit criteria: READINESS-1 through READINESS-3 remain verified;
ENGINE-5 through ENGINE-8 activate only after an explicit Agent Framework
production-support decision; EVENT-1 through EVENT-5 activate only after a
concrete event consumer and transport are selected. Until then, the current
optional-engine and bounded-process-local-event claims remain authoritative.

## Prepared readiness plans

These plans apply safe engineering practices now without activating a new
product commitment or advertising unsupported runtime behavior:

- [Agent Framework production-readiness plan](docs/agent-framework-production-readiness.md) — package isolation, native checkpoint/resume security, stable executor identity, acceptance gates, and release evidence.
- [Event integration readiness plan](docs/event-integration-readiness.md) — transport-neutral CloudEvents rules, idempotency, replay, backpressure, security, and the requirements for a future AsyncAPI binding.

## Intentionally deferred

### A2A push notifications

Push notifications remain deferred because they introduce an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

### Full A2A conformance claim

A broad/full A2A conformance claim remains deferred. The bounded async/streaming profile and advertised-capability evidence are complete, but broader interoperability and conformance gates are outside the current scope. Advertise only the implemented bounded profile.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid obvious future tenant-isolation blockers, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation, token exchange, and consent are deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

## Working rules

- Use the official A2A Project definitions as the source of truth for A2A wire behavior.
- Add or update deterministic tests before marking implementation tasks complete.
- Keep core framework-neutral; engine packages own framework-specific behavior only.
- Route executable workflow operations through the common `SandboxManager`.
- Preserve separate knowledge, memory, session, checkpoint, invocation, approval, schedule, sandbox, and engine-native state lifecycles.
- Production capabilities remain fail-closed until required tests and acceptance gates are green.
- Protocol baseline changes are compatibility changes, not ordinary dependency bumps.
- Authentication and authorization are deployment/runtime configuration; workflows never contain raw credentials.
- Do not claim exactly-once side effects merely because a checkpoint exists; side-effecting callers must be idempotent.
- Run focused checks for each slice and the full CI-shaped matrix for completed meaningful slices, main integration, and release acceptance.

Detailed decisions: `docs/protocol-security-decisions.md`.
Protocol baselines: `docs/protocol-baselines.md`.
Verified implementation state: `PROJECT.md`.
