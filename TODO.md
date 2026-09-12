# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains the scoped fixes, improvements, acceptance gates, and intentionally deferred work.

## Current Phase

**`v0.1.0` is released. `main` remains pre-stable and is focused on deployment acceptance, dependency hygiene, conformance breadth, and evidence for native-engine behavior.**

The public contract remains Open Workflow 1.0.3. The runtime uses a framework-neutral common executor and deployment-selected ADK or LangGraph envelopes; it does not claim task-level native compilation or full Open Workflow/A2A conformance.

## Active backlog

### P0 — Deployment and repository gates

- [ ] **DEPLOY-1** — complete OpenShift-specific SCC/security-context/arbitrary-UID sandbox acceptance against a disposable real OpenShift cluster. The controller and static harness are prepared, but the real-cluster gate is still unverified. Do not advertise OpenShift container execution as accepted until this passes.
- [x] **CI-1** — configure repository branch protection to require the lightweight `Docs / Markdown links` check on every pull request, including documentation-only changes. The workflow now reports the check on every pull request; GitHub displays it as `Docs / Markdown links` and exposes the required API context as `Markdown links`, which is verified on `main`.
- [x] **DEPS-1** — review the six Dependabot updates independently. Pydantic controller updates (#24 and #25), Agent Framework (#26), and base images (#30) passed their applicable gates and were merged. The Actions bundle (#35) was closed because its qemu v4 change failed the release-workflow regression tests; the root bundle (#36) was closed because its lockfile, native, security, and PostgreSQL gates failed. No failing bundle was merged.

### P1 — Portability and conformance evidence

- [x] **TEST-1** — expand the Open Workflow CTK subset to 26 feature files, 48 scenarios, and 96 deterministic ADK/LangGraph executions. Fixtures remain limited to behavior implemented in the common profile, the full-conformance claim remains deferred, and the core coverage gate remains above 90%.
- [x] **ENGINE-2** — add fair engine-specific evidence for compilation cost, sequential and concurrent throughput, checkpoint growth, interruption/resume behavior, and side-effect replay. `benchmarks/engines.py` reports these separately from the common-core benchmark and the locked engine CI matrix uploads the JSON evidence.
- [x] **ENGINE-3** — strengthen ADK resume semantics. The adapter explicitly uses replay with stable operation ids; interruption/resume tests and native probes verify repeated side effects reuse the idempotency key, with durable idempotency guidance in the engine documentation.

### P2 — Operations and maintainability

## Intentionally deferred

### A2A push notifications

Push notifications remain deferred because they introduce an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

### Full A2A conformance claim

A broad/full A2A conformance claim remains deferred. The bounded async/streaming profile and advertised-capability evidence are complete, but broader interoperability and conformance gates are outside the current scope. Advertise only the implemented bounded profile.

### Microsoft Agent Framework production status

The optional adapter remains CI-covered but is not a production image/release target. Independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata remain deferred.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid obvious future tenant-isolation blockers, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation, token exchange, and consent are deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

### AsyncAPI implementation

AsyncAPI 3.1.0 is pinned as a future binding baseline but is not implemented. No active work is planned until a concrete event-integration requirement exists.

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
