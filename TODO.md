# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**v0.1.0 released (2026-08-28) -- remaining work is acceptance- and decision-gated.**

The 2026-08-27/28 sweep landed on `main`, every new gate proved green in CI (CI, Security, External Sandbox, PostgreSQL, kubeconform), and the first formal release **v0.1.0** was cut from the verified head `c47cb86` with all four images published through the Trivy gate. Completed detail lives in `PROJECT.md`, `CHANGELOG.md`, and git history. What remains: Kubernetes/OpenShift real-cluster acceptance (B-006.3, needs a cluster), the B-007 inbound-A2A/streaming decision, and the B-008 third-engine production decision (contract/CTK gates already pass).

### Current integration head

- `main` is at `c47cb86` (tagged `v0.1.0`) and aligned with `origin/main`. Dependabot is active (PRs #18 and #20 rebasing onto the fixed gates; #21 declined -- the runtime intentionally tracks Python 3.12).
- Local verification: root `270 passed, 11 skipped` at `82.75%` coverage, ADK `104 passed`, LangGraph `104 passed`, Agent Framework `139 passed` across the shared contract + CTK surface, all six locks checked, all seven packages built, all four images built and Trivy-scanned locally.
- Remote acceptance for `c47cb86`: CI `33136592588`, Security `33136597832`, External Sandbox `33136592592`, and PostgreSQL `33136592632` are green; Release `33136714445` published all four `0.1.0` images through the Trivy gate and created the GitHub Release. Run IDs and digests are recorded in `PROJECT.md`.
- Untagged green `main` commits publish `latest` and `sha-*` images to GHCR and Docker Hub; a GitHub Release and semantic-version tags appear only when a matching `vX.Y.Z` tag points at the verified commit.

## Active Backlog — Ordered

### SWEEP-VERIFY -- Prove the new CI gates after the sweep commit (P1) -- CLOSED 2026-08-28

The sweep added the Security workflow (pip-audit over all six locked environments), the Trivy publication gate, the kubeconform manifest job, the companion-acceptance publication precondition, controller-image publication, and committed controller locks. None of these has run in CI yet.

- [x] Commit and push the backlog-sweep changes to `main`; confirm the CI, Security, External Sandbox, PostgreSQL, and kubeconform manifest jobs all pass. (Head `c47cb86`: CI `33136592588`, Security `33136597832`, External Sandbox `33136592592`, PostgreSQL `33136592632` all green. Verification caught and fixed three gate bugs: kubeconform needed an explicit skip for the OpenShift-only `Project` kind, the Security audit script had a stderr syntax error, and the companion gate had an unbound variable.)
- [x] Confirm the Release workflow publishes both engine images and both sandbox-controller images through the Trivy gate and the companion-acceptance precondition; record the new run IDs and image digests in `PROJECT.md`. (Release `33136714445`; the Trivy gate correctly blocked two intermediate pushes until base images were refreshed and upstream-owned findings were documented in `.trivyignore`.)
- [x] Confirm Dependabot registers and opens its first update set, and the weekly Security schedule is active. (PRs #18/#20 open with rebase requested; #21 declined -- Python 3.12 is intentional.)

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

`docs/engine-adapter-evaluation.md` selected **Microsoft Agent Framework** as the preferred third-engine candidate. PR #16 merged an optional native adapter and exact `uv.lock`; CI runs it on `main` with a committed-lock check and now also enforces the shared contract and CTK suites natively. It remains deferred as a production image/release target until the production decision and remaining gates are complete.

- [ ] Decide whether Agent Framework is experimental or a production engine; if production, add an independent runtime image, image acceptance, shared contract/CTK coverage, persistence/resume coverage, capability reporting, and release metadata.
- [ ] Pass the shared contract fixtures, applicable CTK/provenance, persistence/resume boundaries, hardened-image acceptance, and release gates before advertising the third engine. (Progress 2026-08-28: the shared contract modules and CTK harness now include the Agent Framework engine wherever its native dependency is installed, and it passes natively -- 139 tests in its CI job, which enforces the shared suites. Remaining: independent runtime image with hardened-image acceptance, persistence/resume coverage, and release metadata, all pending the production decision above.)

### R-001 -- First formal semantic-version release (P3) -- RELEASED 2026-08-28

- [x] After the required production acceptance gates are green, confirm all package versions match the intended release version and create the matching `vX.Y.Z` tag on the verified commit. (`v0.1.0` tagged on `c47cb86`; all packages at 0.1.0, verified by the release prepare job.)
- [x] Verify the Release workflow publishes both engines to GHCR and Docker Hub with exact-version, minor-series, `latest`, and source-SHA tags from one build per engine. (Controllers are GHCR-only at v0.1.0; Docker Hub mirroring for controllers was added for future releases.)
- [x] Verify GitHub Release creation, release notes, OCI version/revision/source labels, SBOM/provenance, and GHCR provenance attestations. (GitHub Release `v0.1.0` created by run `33136714445` after the Trivy gate passed on all four images.)
- [x] Record the release tag, GitHub Actions run, image digests, and pull commands in `PROJECT.md`/end-user documentation. (Digests and run IDs recorded in `PROJECT.md`; pull commands are in the GitHub Release notes and README registry patterns.)

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
