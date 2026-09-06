# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains only active or intentionally deferred work.

## Current Phase

**v0.1.0 is released. Current `main` is unreleased pre-stable work focused on deployment readiness and broader test coverage.**

The public product contract is still stabilizing. External A2A wire behavior targets the official A2A v1 definitions. Open Workflow 1.0.3 keeps its own schema-defined A2A call vocabulary; OWA translates that vocabulary to the selected A2A wire operation at the runtime protocol boundary rather than changing the Open Workflow schema.

## Current Implementation State

Verified implementation detail lives in `PROJECT.md` and is updated after every gate-passing change. Formal release remains `v0.1.0`; current `main` changes are unreleased pre-stable work.

## Active Backlog

### P0 — Deployment readiness

- [ ] **DEPLOY-1** — add OpenShift-specific SCC/security-context/arbitrary-UID sandbox acceptance. Shipped locally: the controller leaves the workload UID unset for OpenShift SCC allocation, and `tests/ci/openshift_sandbox_acceptance.sh` verifies SCC assignment, arbitrary-UID execution, security context, RBAC, network denial, workspace writes, and cleanup. OpenShift-specific enforcement is not checked until the harness passes against a real cluster.
### P1 — Testing

- [ ] **TEST-1** — expand the Open Workflow CTK subset. The selected suite now covers 22 feature files, 42 scenarios, and 84 deterministic executions, including pinned upstream data-flow filtering, HTTP and OpenAPI content/response output projections, caught and uncaught protocol errors, portable `emit`, `listen`, built-in catalog `call`, registered child-workflow `run`, successful and failing HTTP/MCP/A2A/OpenAPI protocol calls, input rejection, flow, policy, transform, validation, nested-input, sequence, and retry scenarios through both engines. Remaining expansion includes broader upstream CTK coverage and other CTK features that are or claim to be implemented.

## Intentionally Deferred

### A2A push notifications

Push notifications remain deferred because they introduce an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

### Full A2A conformance claim

A broad/full A2A conformance claim remains deferred. The bounded async/streaming profile and advertised-capability evidence are complete, but broader interoperability and conformance gates are outside the current scope. Advertise only the implemented bounded profile.

### Microsoft Agent Framework production status

The optional adapter remains CI-covered but is not a production image/release target. Independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata remain deferred.

### Multi-tenancy

Multi-tenancy is outside the current product scope. New security/profile/persistence structures should avoid obvious future tenant-isolation blockers, but no tenant model or tenant-aware behavior should be implemented now.

### Delegated user identity

User delegation/token exchange/consent is deferred until a concrete enterprise A2A/MCP requirement exists. When introduced, use standards-based identity infrastructure rather than custom protocol message fields.

### AsyncAPI implementation

AsyncAPI 3.1.0 is pinned as a future binding baseline but is not implemented. No active work planned.

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
