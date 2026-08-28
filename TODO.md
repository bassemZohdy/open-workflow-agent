# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**Backlog sweep complete (2026-08-27) -- remaining work is verification-, acceptance-, and decision-gated.**

The 2026-08-27 sweep closed the B-006.5 release bookkeeping plus the D-001, D-002, D-003, SEC-001, SEC-002, OPS-001, OPS-002, Q-001, and CORE-001 sections and the B-008 CI slice; completed detail lives in `PROJECT.md`, `CHANGELOG.md`, and git history. What remains: prove the new CI gates once the sweep lands on `main` (below), Kubernetes/OpenShift real-cluster acceptance (B-006.3), the B-007 inbound-A2A/streaming decision, the B-008 third-engine production decision, and R-001 (first formal release once acceptance is ready).

### Current integration head

- `main` is at `80bfa2b` and aligned with `origin/main`; there are no open pull requests. PRs #14 (Kubernetes sandbox boundary + hosted Docker acceptance), #15 (bounded lifecycle SSE), and #16 (Microsoft Agent Framework adapter) are merged.
- Local verification after the sweep: root `270 passed, 11 skipped` at `82.75%` coverage, ADK `104 passed`, LangGraph `104 passed`, Agent Framework `5 passed`, all six locks checked, all seven packages built, both runtime images and both controller images built and smoke-validated.
- Remote acceptance for `80bfa2b` (before the sweep): CI `33105068629`, PostgreSQL `33105068561`, and External Sandbox `33105068565` green; Release `33105399880` published the ADK/LangGraph `latest` and `sha-80bfa2b7887a` images. Run IDs and digests are recorded in `PROJECT.md`.
- Untagged green `main` commits publish `latest` and `sha-*` images to GHCR and Docker Hub; a GitHub Release and semantic-version tags appear only when a matching `vX.Y.Z` tag points at the verified commit.

## Active Backlog — Ordered

### SWEEP-VERIFY -- Prove the new CI gates after the sweep commit (P1)

The sweep added the Security workflow (pip-audit over all six locked environments), the Trivy publication gate, the kubeconform manifest job, the companion-acceptance publication precondition, controller-image publication, and committed controller locks. None of these has run in CI yet.

- [ ] Commit and push the backlog-sweep changes to `main`; confirm the CI, Security, External Sandbox, PostgreSQL, and kubeconform manifest jobs all pass.
- [ ] Confirm the Release workflow publishes both engine images and both sandbox-controller images through the Trivy gate and the companion-acceptance precondition; record the new run IDs and image digests in `PROJECT.md`.
- [ ] Confirm Dependabot registers and opens its first update set, and the weekly Security schedule is active.

### B-006.3 -- Kubernetes/OpenShift external sandbox production acceptance (P1)

The deployment-selected backend, loopback controller boundary, restricted controller image, Kubernetes/OpenShift manifests, and deterministic backend/manifest/controller tests are merged. Docker acceptance is recorded green in `PROJECT.md`; the common external-sandbox contract remains the model. Do not give the main runtime broad cluster permissions.

- [ ] Run acceptance on a real Kubernetes cluster, including timeout/cancellation/restart cleanup, retained-log secret safety, and namespace/RBAC enforcement.
- [ ] Run OpenShift acceptance for SCC/security-context/arbitrary-UID behavior and the same lifecycle/security cases.
- [ ] Advertise Kubernetes/OpenShift container execution through `/v1/capabilities` only after the relevant deterministic and deployment acceptance gates are green.

**B-006 acceptance:** every advertised backend reuses the common sandbox contract, enforces its stated isolation guarantees, keeps infrastructure-native state private, and exposes `run.container` only when the selected backend safely supports it. Kubernetes/OpenShift is a separate acceptance target from Docker.

### B-007 -- Optional inbound A2A exposure and full portable streaming (P2, deferred)

The evaluation baseline is documented in `docs/a2a-streaming-evaluation.md`. Bounded lifecycle SSE is implemented and advertised through `features.lifecycleStreaming`; it is not general token/output streaming. Current outbound A2A calls remain protocol-client functionality only; no inbound A2A server/conformance is advertised.

- [ ] Decide the next bounded slice after B-006 production acceptance: inbound A2A, general portable streaming, or continued deferral.
- [ ] Before inbound A2A, define the engine-neutral Agent Card/skill ownership model plus authentication, authorization, least-privilege scopes, user-delegation boundary, request/content limits, concurrency/rate limits, cancellation, lifecycle correlation, and sanitized error/observability contract.
- [ ] If user delegation is supported, keep it in the deployment identity/security layer; do not hide credentials or delegation tokens inside A2A message payloads.
- [ ] Before advertising general streaming, define one common runtime contract for event ordering, bounded buffering/backpressure, connection/invocation cancellation, disconnect behavior, reconnection/resubscription, terminal errors, byte/time/concurrency limits, and equivalent observable ADK/LangGraph behavior.
- [ ] Extend HTTP/SSE incrementally rather than buffering full streaming responses; add gRPC server streaming only if it can map to the same common contract.
- [ ] Keep push notifications deferred until callback allowlisting, TLS/server identity, authentication, SSRF protection, replay/idempotency, retry/dead-letter policy, and secret-safe logging are defined and tested.
- [ ] Update `/v1/capabilities`, documentation, and contract tests only for the exact bounded features that are actually accepted; do not claim full A2A or streaming conformance prematurely.

### B-008 -- Additional engine adapter (P3, deferred)

`docs/engine-adapter-evaluation.md` selected **Microsoft Agent Framework** as the preferred third-engine candidate. PR #16 merged an optional native adapter and exact `uv.lock`; CI runs it on `main` with a committed-lock check. It remains deferred as a production image/release target until the shared gates are complete.

- [ ] Decide whether Agent Framework is experimental or a production engine; if production, add an independent runtime image, image acceptance, shared contract/CTK coverage, persistence/resume coverage, capability reporting, and release metadata.
- [ ] Pass the shared contract fixtures, applicable CTK/provenance, persistence/resume boundaries, hardened-image acceptance, and release gates before advertising the third engine.

### R-001 -- First formal semantic-version release (P3, when product acceptance is ready)

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
