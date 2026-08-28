# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**v0.1.0 released; bounded inbound A2A shipped; architecture decisions for the next protocol/security slice resolved (2026-08-28).**

The full backlog sweep (documentation, supply-chain, pipeline hardening, quality) landed and proved green in CI, v0.1.0 was released from a verified head, the Kubernetes sandbox backend passed real-cluster acceptance on kind (Kubernetes 1.37, Calico enforcement), and a bounded inbound A2A profile — Agent Card plus synchronous `message/send` with selectable `jsonrpc`/`http_json` transports — is implemented, disabled by default, and guarded by deterministic tests.

The next work is no longer decision-blocked: the architecture review decisions below were resolved on 2026-08-28 and are now implementation backlog.

### Current integration head

- `main` is aligned with `origin/main` at the architecture-review/fix series.
- Local verification before the decision-only documentation update: root `280 passed, 11 skipped` at ≥80% coverage, ADK `104 passed`, LangGraph `104 passed`, Agent Framework `144 passed` across native + shared contract + CTK surfaces, all six locks checked, all seven packages built.
- The current release remains **v0.1.0** (`c47cb86`); green `main` commits keep publishing `latest` and `sha-*` images, and future releases follow the same tag-on-green flow (R-001, closed).

## Active Backlog

### Protocol baseline and compatibility

- [ ] PROTOCOL-1: inventory every external protocol/specification implemented or advertised by the runtime (Open Workflow, A2A, MCP, OpenAPI, CloudEvents, and future gRPC/AsyncAPI bindings), verify the latest stable released specification from its authoritative source, and record the exact pinned baseline used by OWA.
- [ ] PROTOCOL-2: update existing implementations that lag their verified latest-stable baseline; A2A is the first migration and must move from the current legacy `0.3.0` assumptions to the latest stable A2A release before Task/streaming expansion.
- [ ] PROTOCOL-3: add deterministic compatibility/conformance tests for every pinned protocol baseline and advertise the implemented protocol/version only after those gates are green.
- [ ] PROTOCOL-4: retain older protocol behavior only through explicit, tested compatibility adapters/aliases where there is demonstrated interoperability value; never silently float to drafts, RCs, previews, or editor drafts.
- [ ] PROTOCOL-5: add a release/CI check that prevents an unreviewed protocol baseline change from being advertised as supported.

### Security configuration model

- [ ] SECURITY-1: introduce a shared named security-profile configuration model used by inbound and outbound protocol adapters. Authentication mechanisms, credential references, identities, scopes/roles, and authorization policy are deployment/runtime configuration.
- [ ] SECURITY-2: expose security configuration through strict YAML plus the existing `OWA__...` environment-variable override convention. Raw credentials must not be required in workflow definitions; workflows/tools may reference only configured security profiles.
- [ ] SECURITY-3: support environment/secret references for sensitive values and keep them out of logs, plans, capability responses, lifecycle events, and persisted workflow definitions. Production guidance remains Kubernetes/OpenShift Secrets, Docker secrets, Vault/external-secret systems, or equivalent deployment secret managers.
- [ ] SECURITY-4: keep enterprise OAuth2/OIDC, token exchange, and user delegation compatible with edge/identity-provider enforcement. OWA may enforce bounded local authorization but must not become an identity provider.

### A2A next bounded profile

- [ ] A2A-1: migrate Agent Card/discovery/transport metadata to the verified latest stable A2A specification. Claim only a **bounded, spec-version-pinned profile**, never full conformance until the applicable conformance/interoperability gates pass.
- [ ] A2A-2: replace the single shared bearer-only model with configuration-selected named security profiles. First runtime-managed option: named credentials with per-credential skill/action scopes; edge-delegated OAuth2/OIDC remains supported as a deployment model rather than hard-coded protocol logic.
- [ ] A2A-3: support multiple config-declared A2A skills mapped to explicitly registered workflows. Incoming clients must not choose arbitrary workflow paths; routing is deployment-owned by skill id.
- [ ] A2A-4: implement the A2A Task model as a projection over common OWA invocation state rather than a second execution engine. Prefer `task_id == invocation_id`; map OWA `waiting` to A2A `input-required`, `completed` to `completed`, `faulted` to `failed`, and `cancelled` to `canceled` where the pinned specification uses those states.
- [ ] A2A-5: add Task retrieval and cancellation using the common invocation/ExecutionHandle APIs; durable approval/waiting state must remain owned by the existing common runtime stores.
- [ ] A2A-6: add asynchronous Task-returning send semantics only in the form defined by the pinned A2A specification; do not invent an OWA-specific async protocol flag.
- [ ] A2A-7: after the Task model is green, implement A2A streaming/resubscription by mapping protocol Task/message/artifact events onto the existing bounded common lifecycle/event infrastructure. Do not expose engine-native stream/checkpoint objects.
- [ ] A2A-8: add A2A interoperability/conformance coverage for the bounded profile before expanding the capability advertisement.

### Architecture cleanup

- [ ] ARCH-3: rename the portable sandbox-requirements module `sandbox_contract.py` to `sandbox_capabilities.py` (or equivalent capability-oriented name). Keep `sandbox/contract.py` as the runtime backend request/result/interface contract; do not merge the two concepts.

## Intentionally Deferred

### OpenShift sandbox acceptance (deferred 2026-08-28)

Kubernetes acceptance is recorded green (see `PROJECT.md`). OpenShift-specific validation is intentionally skipped for now:

- SCC/security-context/arbitrary-UID behavior on OpenShift;
- the same lifecycle/security cases under OpenShift;
- OpenShift-specific advertisement through `/v1/capabilities` (the sandbox `platform` field already distinguishes `openshift`; it must not be advertised for OpenShift deployments until this acceptance runs).

Revisit when an OpenShift cluster is available; the kind acceptance procedure in `docs/development.md` is the template.

### A2A push notifications and full conformance (deferred 2026-08-28)

Push notifications remain intentionally deferred because they introduce a distinct outbound-webhook trust boundary requiring callback allowlisting, TLS/server identity verification, SSRF controls, callback authentication, replay/idempotency protection, bounded retry/dead-letter behavior, and secret-safe observability.

A full A2A conformance claim also remains deferred until the bounded Task/streaming profile and the applicable interoperability/conformance gates are complete.

### Microsoft Agent Framework production status (deferred 2026-08-28)

The adapter remains an optional package with CI-enforced native, shared contract, and CTK coverage (144 tests green). Deferred by decision: the production-engine work — independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata. The evaluation record lives in `docs/engine-adapter-evaluation.md`.

## Architecture Review — 2026-08-28

Full audit of `main` after the sandbox package refactor, the bounded A2A slice, and the v0.1.0 release, checked against `Project Definition.md`, `AGENTS.md`, and `docs/development.md`.

### Verified clean (evidence)

- Dependency direction: core imports no engine package; engines import core only; no cross-engine imports (grep-verified).
- No engine creates execution paths: zero subprocess/container/controller references in engine packages; engines consume `SandboxManager` through the shared executor only.
- Open Workflow schema: repository copy and bundled copy are byte-identical for the currently pinned baseline; PROTOCOL-1 will verify whether that exact revision remains the latest stable release.
- Internal execution plan never exposed: it lives in application state; public responses carry status/output/sanitized errors and the workflow fingerprint only.
- Lifecycle separation holds: knowledge/memory/approvals/schedules/invocations/sandbox/engine-native state remain isolated stores; engine-native state stays in private per-engine files.
- Sandbox single-path preserved through the package refactor; the layered package keeps implementations in core as shared utilities.
- Fail-closed defaults unchanged: A2A, sandbox, shell, external catalogs, approvals all disabled by default.
- Secret handling: constant-time bearer comparisons (A2A, approvals); no secret logging in new modules; environment secret references fail closed outside configured secret boundaries.
- `trust_env` on outbound clients: ambient proxies allowed for generic protocol calls, disabled where DNS pinning must not be bypassed and on loopback controller transports (deliberate, documented).

### Tasks — gaps fixed

- [x] ARCH-1: Agent Card `url` derived from the request base URL, which was wrong behind reverse proxies/TLS termination. Fixed with `a2a.public_base_url` configuration and http(s) validation.
- [x] ARCH-2: runtime version strings were triple-sourced. Fixed with `core/src/open_workflow_agent/_version.py` as the single runtime version source plus CI/release consistency checks.

### Decisions resolved — 2026-08-28

- [x] DEC-1: **protocol version policy** — use the latest stable released version of every external protocol/specification, pin the verified baseline in the project, and upgrade deliberately through compatibility review/tests. No draft/RC/preview baseline as the production contract.
- [x] DEC-2: **authentication/authorization** — security mechanisms and policy are externalized runtime/deployment configuration through YAML/environment overrides. Use reusable named security profiles; credentials are secret references, not workflow data. Named scoped credentials are the first bounded A2A runtime option; enterprise OAuth2/OIDC/delegation remains compatible with edge/identity-provider enforcement.
- [x] DEC-3: **A2A skill ownership** — expose multiple registered workflows as config-declared A2A skills, routed by skill id. Clients cannot select arbitrary workflow files/paths.
- [x] DEC-4: **A2A Task model** — map Tasks onto common invocations/ExecutionHandles, prefer task id = invocation id, expose get/cancel, surface waiting/approval as input-required, and add spec-native asynchronous/streaming behavior only after the Task contract is established.
- [x] DEC-5: **sandbox contract naming** — keep the two abstractions separate and rename the portable-requirements module to a capability-oriented name instead of merging it with `sandbox/contract.py`.

The detailed policy record is `docs/protocol-security-decisions.md`.

Known soft spots already documented and accepted (not re-tasked): NetworkPolicy enforcement is deployment-asserted and not runtime-verified (`features.sandbox` trusts `network_policy_enforced`); non-deadline controller failures surface as generic `sandbox_process_error` 500s.

## Working Rules

- Add or update tests before marking backlog items complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Route executable workflow operations through the common `SandboxManager`; engines must never create independent subprocess/Docker/Kubernetes execution paths.
- Treat the internal sandbox as a controlled execution boundary, not a hard isolation boundary; advertise only controls actually enforced by the selected backend/platform.
- Do not give the main runtime unrestricted Docker socket or cluster-wide Kubernetes/OpenShift access.
- Keep backend selection deployment-controlled; workflow definitions must not choose infrastructure backends directly.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, schedule, sandbox execution, and engine-native state lifecycles.
- Do not require paid model/API access or install dependencies at container startup/runtime execution.
- Keep production capabilities fail-closed until deterministic contract tests and the relevant release/deployment acceptance gates are green.
- Treat protocol baseline changes as reviewed compatibility changes, not ordinary dependency bumps.
- Keep authentication and authorization policy externalized in strict runtime/deployment configuration; never require raw credentials in workflow definitions.
