# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains the scoped backlog and intentionally deferred work.

## Current Phase

**`v0.2.0` is the selected release candidate. A2A task ownership is implemented and verified; exact-commit release gates and publication remain.**

The public contract remains Open Workflow 1.0.3. The runtime uses a framework-neutral common executor and deployment-selected ADK or LangGraph envelopes; it does not claim task-level native compilation or full Open Workflow/A2A conformance.

## Active backlog

### Next milestone — release hardening and A2A task access

This milestone prepares the current bounded profile for another formal release.
Work is ordered: decide the A2A task-access boundary first, implement and test
the chosen boundary where required, then run release gates on one exact commit
and publish only after those gates are recorded. Do not pull push
notifications, full protocol conformance, multi-tenancy, delegated identity,
AsyncAPI, or Agent Framework production packaging into this milestone.

#### P0 — A2A task-access decision and enforcement

- [x] **A2A-8** — decide and document whether deployments with multiple A2A principals support per-task ownership. The selected contract binds task ownership to the authenticated principal and enforces it for get, cancel, subscribe, and resume, with a trusted unauthenticated single-principal mode for ownerless legacy handles. The decision is recorded in `Project Definition.md`, `PROJECT.md`, `docs/api.md`, and `docs/configuration.md`.
- [x] **A2A-9** — implement the boundary selected by `A2A-8` without exposing task existence, credentials, or engine-native state. The same rule applies to JSON-RPC and HTTP+JSON `GetTask`, `CancelTask`, `SubscribeToTask`, and resuming `SendMessage`, while common invocation/task projection remains authoritative.
- [x] **A2A-10** — add deterministic security and contract coverage for authorized access, cross-principal denial, unknown-task behavior, cancellation, subscription, both transports, and sanitized/non-disclosing errors; root and engine suites pass. Existing waiting-task resume coverage remains in the common A2A async suite.

#### P1 — Exact-commit release readiness

- [ ] **RELEASE-2** — run [docs/release-readiness.md](docs/release-readiness.md) against one exact candidate commit. Verify version/tag metadata, current `CHANGELOG.md`/`PROJECT.md`/`TODO.md`, clean source state, every independent lock, root quality and coverage gates, ADK/LangGraph/Agent Framework evaluation suites, documentation links, Docker/runtime restart-resume acceptance, external sandbox and PostgreSQL gates, dependency/image scans, and SBOM/provenance evidence. Record run identifiers, results, exceptions, and the candidate SHA in `PROJECT.md`.
- [ ] **RELEASE-3** — publish the next formal release only after `A2A-8` through `A2A-10` and `RELEASE-2` are green. Select and record the SemVer version, merge the release commit to `main`, create the matching Git tag/GitHub Release, publish immutable runtime/controller image references, and record rollback references and digests in `PROJECT.md`.

#### P2 — Post-release architecture decision

- [ ] **ENGINE-4** — decide whether native-engine depth is a product requirement after the release. Either retain the current native execution-envelope architecture and keep its capability/documentation language narrow, or define a separate task-level plan-to-native-node/compiler milestone, starting with LangGraph’s stronger native resume path and then proving ADK parity. Do not change the public DSL or expose the internal execution plan.

Milestone exit criteria: the A2A task-access contract is explicit and tested,
the exact release candidate has complete evidence, and the release decision is
recorded. `ENGINE-4` may remain open after publication as a separate architecture
track.

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
