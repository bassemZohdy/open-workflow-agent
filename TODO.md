# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains only active or intentionally deferred work.

## Current Phase

**v0.1.0 is released. Current `main` is unreleased pre-stable work focused on shared security integration, deployment-declared A2A skills, and the remaining A2A Task/streaming profile.**

The public product contract is still stabilizing. External A2A wire behavior targets the official A2A v1 definitions. Open Workflow 1.0.3 keeps its own schema-defined A2A call vocabulary; OWA translates that vocabulary to the selected A2A wire operation at the runtime protocol boundary rather than changing the Open Workflow schema.

## Current Implementation State

Verified implementation detail lives in `PROJECT.md` and is updated after every gate-passing change. Formal release remains `v0.1.0`; current `main` changes are unreleased pre-stable work.

## Active Backlog

### P0 — Protocol baseline completion

- [ ] **PROTOCOL-3** — complete external compatibility/interoperability evidence for every advertised pinned baseline before any broad conformance claim.
  - Deterministic local baseline/shape/advertisement tests: complete.
  - Engine-shared Open Workflow CTK/contract coverage: complete for the portable profile.
  - Broad external A2A/MCP/OpenAPI conformance/interoperability suites: not claimed yet.

### P0 — Shared security configuration

- [ ] **SECURITY-1** — integrate reusable named security profiles across inbound/outbound protocol adapters. Done: A2A inbound bearer (`a2a.security_profile`), approvals operator check (`approvals.operator_security_profile`), external-catalog authentication (`authentication.security_profile`), and per-tool references (`tools[].security_profile`) with OpenAPI/A2A/MCP header forwarding. Remaining: OAuth2 client-credentials/mTLS profile types for outbound adapters, and MCP stdio (disabled) has no auth surface.
- [x] **SECURITY-2** — expose profiles through the main strict runtime YAML plus `OWA__...` overrides. `RuntimeConfig.security` is a strict-parsed section; `OWA__SECURITY__...` overrides work through the existing generic environment-override mechanism. Protocol/tool configuration references profiles by name; workflow definitions never contain raw credentials.
- [ ] **SECURITY-3** — complete secret-safe integration across adapters/logs/plans/capabilities/Agent Cards/lifecycle/A2A Tasks/sandbox/persistence. Env-only `SecretReference` resolution and secret-safe validation errors are implemented; full adapter-path verification remains.
- [ ] **SECURITY-4** — wire standard authorization checks into protocol actions/skills. Framework-neutral principal/role/scope/action/resource/audience policy evaluation is implemented and tested; A2A/MCP enforcement remains. Depends on `A2A-3` (skills) for meaningful per-resource checks.
- [ ] **SECURITY-6** — remove temporary protocol-specific credential fields as shared profiles replace them. `A2AConfig.auth_token` and `ApprovalConfig.operator_token` are removed (replaced by named profile references). Remaining ad-hoc fields: external-catalog `bearer_token_env`/`basic_*_env` (basic has no profile type yet) and the legacy global `OWA_BEARER_TOKEN_ENV` protocol-client environment.

### P1 — Traffic policy

- [ ] **TRAFFIC-1** — introduce a separate deployment-controlled `traffic_policy` model for rate limits, concurrency limits, burst/admission control, and future circuit policies. Authentication/authorization profiles must not own traffic management.

### P1 — A2A next bounded profile

- [ ] **A2A-2** — replace the temporary bearer-only model with shared named security profiles and per-principal skill/action authorization. Authentication half is done (`a2a.security_profile` resolves a named `bearer` profile); per-principal skill/action authorization still depends on `A2A-3` + `SECURITY-4`.
- [x] **A2A-3** — support multiple deployment-configured A2A skills mapped only to explicitly registered workflows. Clients must never select arbitrary workflow paths/files/catalog entries. (`a2a.skills` entries reference uniquely registered `workflow.catalog` names; the Agent Card advertises exactly those skills, `message.metadata.skillId` routes, and unknown/ambiguous references fail closed at startup or request time.)
- [ ] **A2A-6** — map waiting/input-required/resume and protocol-native asynchronous behavior to the A2A Task model. Follow official `SendMessageConfiguration.returnImmediately`; do not invent an OWA-specific async flag.
- [ ] **A2A-7** — after Task state/authorization are green, implement A2A streaming/resubscription over the common lifecycle/event infrastructure. Never expose engine-native checkpoint or stream objects.
- [ ] **A2A-8** — add external A2A interoperability/conformance evidence and capability-accuracy tests before expanding advertisement beyond the bounded Task profile.

### P2 — Maintenance

- [ ] **MAINT-1** — review Dependabot PR #22 (Docker base image `python:3.12-slim` → `python:3.14-slim`); confirm image builds and External Sandbox CI acceptance still pass before merging, or close/defer with a reason if not yet desired.

### Recommended implementation order for remaining A2A work

```text
shared security RuntimeConfig/adapters
  -> deployment-declared skills + per-skill authorization
  -> waiting/input-required + resume mapping
  -> SendMessageConfiguration.returnImmediately async behavior
  -> message/task streaming + resubscription
  -> interoperability/conformance gates
```

The current blockers for broader A2A streaming/async advertisement are **shared authorization + skill routing + portable waiting/resume/async semantics**, not engine-native streaming.

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
