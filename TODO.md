# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains the scoped fixes, improvements, acceptance gates, and intentionally deferred work.

## Current Phase

**`v0.1.0` is released. `main` remains pre-stable and is focused on deployment acceptance, dependency hygiene, conformance breadth, and evidence for native-engine behavior.**

The public contract remains Open Workflow 1.0.3. The runtime uses a framework-neutral common executor and deployment-selected ADK or LangGraph envelopes; it does not claim task-level native compilation or full Open Workflow/A2A conformance.

## Completed in this audit

- [x] **FIX-1** — make the default FastEmbed provider lazy with respect to optional NumPy. Minimal/core-only and engine environments can construct `RuntimeServices`; indexing and search fail with a bounded `KnowledgeError` when the knowledge extra is absent.
- [x] **FIX-2** — include parser, chunking, and embedding identities in knowledge manifest reuse checks so configuration or parser changes trigger re-indexing.
- [x] **SEC-1** — make deployment-owned authentication and operation/idempotency headers authoritative over workflow/tool payload headers; reject credentials embedded in protocol endpoints.
- [x] **SEC-2** — harden A2A public-base URL validation against malformed ports, credentials, queries, and fragments; normalize protocol allowlist host matching.
- [x] **VALIDATE-1** — reject non-positive protocol timeouts before they are reduced to a misleading near-zero network timeout.
- [x] **DOCS-1** — correct the architecture guide and engine READMEs so sandbox and native-engine claims match the implementation.
- [x] **CI-1a** — add a lightweight Markdown relative-link workflow for documentation-only changes, while keeping the full code/test workflow scoped to code changes.

## Active backlog

### P0 — Deployment and repository gates

- [ ] **DEPLOY-1** — complete OpenShift-specific SCC/security-context/arbitrary-UID sandbox acceptance against a disposable real OpenShift cluster. The controller and static harness are prepared, but the real-cluster gate is still unverified. Do not advertise OpenShift container execution as accepted until this passes.
- [ ] **CI-1** — configure repository branch protection to require the lightweight `Docs / Markdown links` check when a documentation-only pull request is evaluated. The workflow is committed; the GitHub branch-protection endpoint is unavailable to the current integration, so the repository setting remains an administrator action.
- [ ] **DEPS-1** — review the five open Dependabot updates (root dependencies, ADK, both controller Pydantic updates, and Docker base image) independently. Merge only after the affected lockfile, native/contract tests, security scan, image acceptance, and release implications are verified; close or defer updates that do not meet those gates.

### P1 — Portability and conformance evidence

- [ ] **TEST-1** — expand the Open Workflow CTK subset beyond the current 22 feature files, 42 scenarios, and 84 deterministic ADK/LangGraph executions. Add fixtures only for behavior implemented in the common profile, keep the full-conformance claim explicitly deferred, and preserve the 90% core coverage gate.
- [ ] **ENGINE-1** — choose and document the native-engine depth required for the next milestone. Either implement task-level plan-to-native-node compilation for both production engines with task-boundary checkpoint/cancellation tests, or formally retain the current native execution-envelope architecture and adjust capability/reporting language accordingly.
- [ ] **ENGINE-2** — add fair engine-specific evidence for compilation cost, sequential and concurrent throughput, checkpoint growth, interruption/resume behavior, and side-effect replay. Keep common-core benchmark results separate from native-framework comparisons.
- [ ] **ENGINE-3** — strengthen ADK resume semantics. Prove task-boundary continuation without replay where the framework supports it, or keep replay behavior explicit and add durable idempotency guidance/tests for every side-effecting operation.

### P2 — Operations and maintainability

- [ ] **OPS-1** — make background knowledge-watch failures observable and recoverable: retain the last reload error in bounded runtime health/metrics state, log only safe metadata, and define whether a later watch cycle retries after failure.
- [ ] **OPS-2** — add a release-readiness checklist that records the exact commit, lock checks, core/engine matrices, external-sandbox/PostgreSQL gates, image scan results, and unresolved acceptance blockers before publishing a new version.

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
