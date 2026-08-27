# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**B-006 — External sandbox backends and production acceptance.**

The B-005 internal sandbox foundation/hardening and the B-004 external-catalog implementation are merged. The Docker external sandbox baseline is also merged: the common backend-neutral sandbox requirements/capability contract, deployment-controlled `docker` backend, restricted Unix-socket controller boundary, exact approved-image policy, fail-closed `run.container` routing, controller-side isolation policy, deterministic unit/security coverage, shared ADK/LangGraph container contracts, and path-scoped external-sandbox CI are present on `main`.

Kubernetes/OpenShift sandbox execution remains unimplemented. Inbound A2A exposure, portable streaming, and a third runtime engine remain intentionally deferred and are not advertised as production capabilities.

### Current integration head

- `main` is currently at `2855e0deadc7ee9b9072f46b3f3847d54eb600fe`; there are no open pull requests.
- PR #13 merged the external-sandbox baseline to `main` as code integration commit `5ff07363887505da7017c872289cb7c2dc6ecfdc`.
- The historical `work/durable-hitl` branch still appears diverged because its seven implementation commits were squash-merged to `main` as commit `70e30d5c49a24a8549af41539ce20d0b3414f719`; the squash commit tree matches that branch head, so it does not represent missing HITL code.
- PostgreSQL CI run `33039622426` is green: common-store integration and ADK/LangGraph PostgreSQL container persistence/restart acceptance all passed.
- Main CI run `33039622437` exposed a real LangGraph event-delivery race: a listener could advertise `waiting` before the in-memory event bus had registered its subscriber. The common event bus now registers synchronously before returning the awaitable, regression coverage was added, and the shared eventing contract now fails fast instead of consuming the full workflow timeout (`0818f832684424877b79a62c7ab8d5d8aba15196`, `531c3c9299de3785ab3867d7d7cd56937b0d8f7b`, `2855e0deadc7ee9b9072f46b3f3847d54eb600fe`).
- Latest main CI run `33040262632` targets `2855e0deadc7ee9b9072f46b3f3847d54eb600fe` but is currently pending with zero jobs allocated. This must be resolved/verified before the eventing fix and merged head can be closed as green.
- External Sandbox CI run `33039622427` has green common contract/security checks. ADK/LangGraph Docker external-sandbox acceptance remains queued on the self-hosted Linux/x64/Docker runner and must pass before Docker external sandbox production acceptance is recorded.
- The Release workflow must be verified after successful `main` CI completion. Untagged green `main` code commits are expected to publish `latest` and `sha-*` images to GHCR and Docker Hub. A GitHub Release and semantic-version image tags are created only when a matching `vX.Y.Z` tag points at the verified commit.

## Active Backlog — Ordered

### B-006.5 — Close current-head CI and release verification (P1)

- [ ] Investigate and resolve GitHub Actions scheduling for CI run `33040262632` if it remains pending with zero jobs allocated; distinguish GitHub-hosted scheduling/capacity issues from repository workflow configuration before changing CI logic.
- [ ] Verify CI run `33040262632` (or a clean replacement run for the same/following accepted `main` head) completes green for root quality gates, both engine-native/shared contract suites, applicable CTK scenarios/provenance, hardened Docker-image acceptance, knowledge, external-catalog image acceptance, internal-sandbox image acceptance, and genuine stop/restart/resume behavior.
- [ ] Confirm the LangGraph eventing regression is closed by proving the previously hanging `test_emit_and_listen_have_equivalent_common_results` path completes for both engines without relying on the outer eight-minute process timeout.
- [ ] Verify External Sandbox CI run `33039622427` completes green for both ADK and LangGraph Docker external-sandbox acceptance on the self-hosted Docker runner; both Docker jobs are currently queued.
- [ ] Verify the Release workflow triggered by a successful accepted `main` CI publishes both engine images to GHCR and Docker Hub with `latest` and the matching `sha-*` tags, correct OCI metadata, SBOM/provenance, and GHCR build provenance attestations.
- [ ] Record the final CI/External Sandbox/PostgreSQL/Release run IDs, image digests, and acceptance status in `PROJECT.md`; remove these verification items from this file after they are proven green.

### B-006.3 — Kubernetes/OpenShift external sandbox backend (P1)

The common external-sandbox contract and Docker backend must remain the model. Do not create engine-specific subprocess/container paths and do not give the main runtime broad cluster permissions.

- [ ] Add a deployment-selected Kubernetes/OpenShift sandbox backend behind the existing `SandboxManager` and backend-neutral request/result/capability contract.
- [ ] Use a dedicated sandbox namespace/project and narrowly scoped ServiceAccount/controller permissions limited to the ephemeral sandbox workload lifecycle; the runtime must not receive cluster-wide workload-management permissions.
- [ ] Enforce approved immutable images, non-root/arbitrary-UID execution, no privileged mode, no host PID/IPC/network namespaces, no `hostPath`, no unrestricted service-account token use, read-only root filesystem where practical, bounded ephemeral storage, CPU/memory/process/output/time limits, and deployment-owned secret references.
- [ ] Add NetworkPolicy/egress policy support and fail closed when a requested network-isolation guarantee cannot be enforced.
- [ ] Implement cancellation, timeout, log/output bounds, cleanup/TTL, runtime shutdown handling, and restart/ambiguous-failure semantics without exposing pod/job/native IDs in the public API contract.
- [ ] Add deterministic backend policy/unit tests plus shared ADK/LangGraph `run.container` contract fixtures proving engine-independent observable behavior.
- [ ] Add Kubernetes acceptance and OpenShift-specific SCC/security-context/arbitrary-UID acceptance, including timeout/cancellation/restart cleanup and retained-log secret-safety checks.
- [ ] Advertise Kubernetes/OpenShift container execution through `/v1/capabilities` only after the relevant deterministic and deployment acceptance gates are green.

**B-006 acceptance:** at least one stronger external backend is production-accepted and every advertised backend reuses the common sandbox contract, enforces its stated isolation guarantees, keeps infrastructure-native state private, and exposes `run.container` only when the selected backend safely supports it. Docker acceptance must be recorded green before the Docker backend is treated as production-accepted; Kubernetes/OpenShift remains a separate acceptance target.

### D-001 — Reconcile authoritative documentation with implemented capabilities (P1)

`Project Definition.md` is authoritative, but parts of its Future Portable Profile still describe durable HITL and external catalog support as deferred/disabled even though bounded implementations have since been merged. The authoritative description must not lag the runtime.

- [ ] Reconcile `Project Definition.md` with the implemented durable approval/HITL behavior and the merged external-catalog profile, using current code, contract tests, `/v1/capabilities`, and `PROJECT.md` as evidence; remove only statements that are actually superseded.
- [ ] Clearly distinguish process-local generic eventing from durable approval state/replay and document the exact operator authorization, expiry/idempotency, persistence, and portability boundaries that are implemented.
- [ ] Document the exact accepted external-catalog trust/allowlist/pinning/cache/failure boundaries and keep unsupported remote behaviors explicitly fail-closed.
- [ ] Recheck README/end-user/API/configuration/deployment documentation so advertised capabilities, examples, environment variables, and limitations match the authoritative definition and current runtime behavior.

### OPS-001 — CI runner and repository governance hardening (P2, before formal release)

- [ ] Ensure a maintained self-hosted runner with labels `[self-hosted, linux, x64, docker]` is online for Docker/container acceptance and external-sandbox workflows; document runner bootstrap, required Docker access, isolation expectations, patching, cleanup, and failure recovery.
- [ ] Confirm GitHub-hosted CI jobs start reliably after the current pending-zero-jobs incident; if repository settings or workflow concurrency are contributing, correct them and add a small operational note to `PROJECT.md`/developer documentation.
- [ ] Add a `main` branch protection rule or repository ruleset before the first formal release: require the stable CI status checks, prevent force-push/deletion, and define the controlled maintainer/admin exception path without blocking release automation.
- [ ] Audit and prune stale merged/superseded branches after verifying their content is present on `main`, including old feature/CI/docs branches and scratch/tmp branches; retain a branch only when it still represents intentional unmerged work.

### B-007 — Optional inbound A2A exposure and portable streaming (P2, deferred)

The evaluation baseline is documented in `docs/a2a-streaming-evaluation.md`. Current bounded outbound A2A calls remain protocol-client functionality only; no inbound A2A server/conformance or common streaming capability is advertised.

- [ ] Decide the first bounded implementation slice after B-006 production acceptance: inbound A2A exposure, common streaming, or continued deferral.
- [ ] Before inbound A2A, define the engine-neutral Agent Card/skill ownership model plus authentication, authorization, least-privilege scopes, user-delegation boundary, request/content limits, concurrency/rate limits, cancellation, lifecycle correlation, and sanitized error/observability contract.
- [ ] If user delegation is supported, keep it in the deployment identity/security layer; do not hide credentials or delegation tokens inside A2A message payloads.
- [ ] Before advertising streaming, define one common runtime contract for event ordering, bounded buffering/backpressure, connection/invocation cancellation, disconnect behavior, reconnection/resubscription, terminal errors, byte/time/concurrency limits, and equivalent observable ADK/LangGraph behavior.
- [ ] Implement HTTP/SSE incrementally rather than buffering full streaming responses; add gRPC server streaming only if it can map to the same common contract.
- [ ] Keep push notifications deferred until callback allowlisting, TLS/server identity, authentication, SSRF protection, replay/idempotency, retry/dead-letter policy, and secret-safe logging are defined and tested.
- [ ] Update `/v1/capabilities`, documentation, and contract tests only for the exact bounded features that are actually accepted; do not claim full A2A or streaming conformance prematurely.

### B-008 — Additional engine adapter (P3, deferred)

`docs/engine-adapter-evaluation.md` provisionally selects **Microsoft Agent Framework** as the preferred third-engine candidate. No dependency is enabled yet.

- [ ] After B-006 acceptance and B-007 boundary finalization, run a dependency/lock/image-size spike using the smallest stable Microsoft Agent Framework core/workflow packages that satisfy the existing engine SPI.
- [ ] Confirm Python 3.12 compatibility, stable graph/workflow APIs, asynchronous cancellation, native checkpoint/resume capability, permissive licensing, and no forced Azure/provider dependency leakage into common contracts.
- [ ] Implement an independent `engines/agent-framework` package with its own exact lock and a separate `open-workflow-agent-agent-framework` image; core must remain framework-neutral.
- [ ] Compile the existing immutable `WorkflowPlan` through the current `WorkflowEngine` SPI; do not introduce another workflow DSL, model contract, tool contract, or public engine-native state.
- [ ] Pass the same shared contract fixtures, applicable CTK subset/provenance, persistence/resume boundaries, capability reporting, hardened-image acceptance, and release gates before advertising the third engine.

### R-001 — First formal semantic-version release (P3, when product acceptance is ready)

The release workflow already supports continuous verified `latest`/`sha-*` image publication from green `main`. The repository currently has no GitHub Release; a formal release should be cut only from a fully accepted commit.

- [ ] After the required production acceptance gates are green, confirm all package versions match the intended release version and create the matching `vX.Y.Z` tag on the verified commit.
- [ ] Verify the Release workflow publishes both engines to GHCR and Docker Hub with exact-version, minor-series, `latest`, and source-SHA tags from one build per engine.
- [ ] Verify GitHub Release creation, release notes, OCI version/revision/source labels, SBOM/provenance, and GHCR provenance attestations.
- [ ] Record the release tag, GitHub Actions run, image digests, and pull commands in `PROJECT.md`/end-user documentation.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Route executable workflow operations through the common `SandboxManager`; engines must never create independent subprocess/Docker/Kubernetes execution paths.
- Treat the internal sandbox as a controlled execution boundary, not a hard isolation boundary; advertise only controls actually enforced by the selected backend/platform.
- Do not give the main runtime unrestricted Docker socket or cluster-wide Kubernetes/OpenShift access.
- Keep backend selection deployment-controlled; workflow definitions must not choose infrastructure backends directly.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, schedule, sandbox execution, and engine-native state lifecycles.
- Do not require paid model/API access or install dependencies at container startup/runtime execution.
- Keep production capabilities fail-closed until deterministic contract tests and the relevant release/deployment acceptance gates are green.
