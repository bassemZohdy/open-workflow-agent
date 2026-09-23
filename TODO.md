# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains the scoped backlog and intentionally deferred work.

## Current Phase

**`v0.2.0` is the current formal release. The current open-source core scope is complete; optional integration hardening tracks are documented for future adopters and maintainers.**

The public contract remains Open Workflow 1.0.3. The runtime uses a framework-neutral common executor and deployment-selected ADK or LangGraph envelopes; it does not claim task-level native compilation or full Open Workflow/A2A conformance.

## Active backlog

The verified runtime, ADK/LangGraph engines, bounded A2A profile, and current
process-local event baseline remain the supported project surface. The
2026-09-24 release review found a bounded maintenance milestone in the
publication pipeline; it does not activate an optional product track.

### P0 — Release integrity maintenance

Work in order. Evidence and findings are in
[the release integrity review](docs/release-integrity-review-2026-09-24.md).

- [x] **RELEASE-4** — record the verified 2026-09-24 maintenance publication
  at commit `95aeb02` in `PROJECT.md`: exact green CI/companion/release
  runs and immutable GHCR digests for all four images. Keep `v0.2.0` as the
  current formal release and distinguish its tag from rolling `sha-*` tags.
- [ ] **RELEASE-5** — require exact-head External Sandbox and PostgreSQL
  acceptance before publishing a `main` commit. Ensure those workflows run
  for every publishable `main` push; wait for their same-commit conclusions
  with a finite deadline and fail closed on failure, absence, or timeout.
  Verify success, failure, and delayed-run decisions deterministically.
- [ ] **RELEASE-6** — preflight all four images on both `linux/amd64` and
  `linux/arm64` with the existing High/Critical OS/library scan before any
  registry push. A failed platform scan must prevent every publish job.
  Preserve independent ADK/LangGraph images, controller boundaries, and
  SBOM/provenance gates. Verify the job dependency graph and both-platform
  scan matrix.
- [ ] **RELEASE-7** — make GitHub Release creation depend on successful
  runtime *and* controller publication. Update deployment/release wording to
  describe exact commit acceptance, both-platform scans, and the remaining
  possibility of partial publication if a registry push fails after preflight.
  Run focused workflow tests, docs validation, and CI before closing the
  milestone.

### Readiness foundation (complete)

This milestone applies safe engineering practices without changing the public
Open Workflow 1.0.3 contract or advertising unsupported runtime behavior.

#### P0 — Safe foundation (complete)

- [x] **READINESS-1** — record the Agent Framework support-tier decision and activation criteria: it remains an optional CI/evaluation adapter, not a production engine, until users, support expectations, deployment environments, compatibility promise, and rollback ownership are explicitly chosen.
- [x] **READINESS-2** — record the transport-neutral event contract baseline: CloudEvents-shaped identity, schema/version policy, correlation, idempotency, replay, backpressure, redaction, authentication, TLS, and bounded delivery rules.
- [x] **READINESS-3** — cross-link the readiness plans through the backlog, project authority, API/protocol baselines, deployment, troubleshooting, README, engine documentation, and changelog; verify the documentation links and diff.

## Optional future tracks (not required for the current core scope)

These tracks are intentionally recorded without unchecked task boxes: they are
not pending work for the project’s current scope. Start them only when a real
user/deployment requirement justifies the added support or integration surface.

### Agent Framework optional community hardening

- **ENGINE-5** — if maintainers elect to offer stronger Agent Framework support,
  audit the isolated dependency graph, license posture, provider exclusions,
  image size, startup behavior, and package/lock reproducibility. Do not add a
  production image before the audit is recorded.
- **ENGINE-6** — implement adapter-owned native checkpoint/resume integration
  using trusted private storage, stable logical executor identities, restricted
  serialized types, and no public framework-native state. Preserve the common
  invocation/resume contract and `SandboxManager` boundary.
- **ENGINE-7** — add deterministic native tests for execution, cancellation,
  checkpoint creation, restart, rehydration, resume, stable operation identity,
  side-effect replay/idempotency, capability reporting, and failure
  sanitization; run common contract and Portable Profile CTK suites.
- **ENGINE-8** — build and accept an independent Agent Framework runtime image
  only after ENGINE-5 through ENGINE-7 are green; pass hardened-image, runtime,
  vulnerability, SBOM, provenance, multi-platform, rollback, and release-
  documentation gates before advertising that optional image.

### External event integration and AsyncAPI

- **EVENT-1** — when a real consumer exists, capture its use case, selected
  transport/broker, delivery semantics, ordering, retention, replay, failure
  policy, security policy, payload limits, operational owner, and
  tenant/delegated-identity impact.
- **EVENT-2** — define implemented event types and versioned JSON Schemas
  against the existing CloudEvents envelope, including correlation/causation,
  redaction, idempotency, compatibility, and loss/retry semantics.
- **EVENT-3** — implement the selected transport adapter with deployment-owned
  authentication/TLS/policy, bounded queues and retries, replay/dead-letter
  handling, metrics, and interoperability tests. Do not add a broker dependency
  before EVENT-1 selects the transport.
- **EVENT-4** — author and validate an AsyncAPI 3.1 document describing only
  implemented channels, operations, messages, schemas, servers, and protocol
  bindings. AsyncAPI is not a substitute for selecting or implementing a
  transport.
- **EVENT-5** — update capabilities, deployment/API documentation, security
  review, CI validation, producer/consumer acceptance, rollback references, and
  release metadata only after EVENT-1 through EVENT-4 are green.

Milestone exit criteria: READINESS-1 through READINESS-3 are verified; no
ENGINE or EVENT track is required for the current open-source core. Until a
future track is explicitly activated, the current optional-engine and bounded
process-local-event claims remain authoritative.

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
