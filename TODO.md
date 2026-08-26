# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**B-005 — Internal sandbox hardening and production acceptance (in progress).**

The B-005 foundation is implemented on `main`: the common sandbox contract, strict disabled-by-default policy, `InternalSandboxBackend`, bounded inline Python `run.script`, separately gated direct-executable `run.shell`, engine-neutral sandbox errors, cancellation/resource/output/workspace controls, `/v1/capabilities`, scheduler integration, and shared ADK/LangGraph contract coverage are present.

The foundation is **not yet production-complete**. Remaining work is concentrated in the managed-function capability boundary, sandbox lifecycle/retry metadata, security-negative coverage, and sandbox-enabled release-image acceptance.

**Next planned phase after B-005 acceptance:** **B-006 — External sandbox backends** (Docker first, then Kubernetes/OpenShift). `run.container` remains unsupported until a container-capable external backend passes its own acceptance gates.

### Verified implementation baseline

- B-003 bounded eventing, lifecycle CloudEvents, durable scheduling, local sub-workflows, and durable HITL approval slices remain complete.
- B-004 secure external-catalog resolver/configuration/cache/cross-engine implementation is complete; two release/container acceptance items remain as carry-over gates below.
- B-005 common `SandboxManager` / `SandboxBackend` / request / result contracts and engine-neutral error classes are implemented in core.
- B-005 strict `SandboxConfig` is implemented with `extra=forbid`, internal-backend selection, disabled-by-default execution, explicit Python runtime support, separate shell gate, environment/secret references, byte limits, workspace limits, timeout, and POSIX resource limits where available.
- B-005 `InternalSandboxBackend` uses direct argv process creation, an execution-scoped temporary workspace, filtered environment, `close_fds`, bounded input/output/workspace/time, process-group cancellation, supported POSIX resource limits, and best-effort cleanup/shutdown.
- B-005 executable Open Workflow tasks route through the common `SandboxWorkflowExecutor` and `SandboxManager`; ADK and LangGraph do not own independent subprocess execution paths.
- B-005 inline Python `run.script` and gated direct-executable `run.shell` have shared ADK/LangGraph parity fixtures; `run.container` and external script resources remain fail-closed.
- CI hardening is merged: root-first dependency gating, matrix fail-fast, bounded job/process timeouts, verbose test diagnostics, and cancellation of superseded non-`main` runs. Main CI run `32969661814` / CI #154 passed on commit `573f4bc26d698f2959affdeb0585745d665f641a`.

## Active Backlog — Ordered

### B-005 — Finish internal sandbox hardening and acceptance (P1)

#### B-005.3 — Complete the managed-function capability boundary

The architecture is documented, but `CatalogContext` still exposes broad runtime services to managed functions. Keep trusted built-ins managed in-process, while narrowing what future executable/plugin-style functions can receive.

- [ ] Replace broad `CatalogContext.services: Any` access for managed/catalog functions with explicit capability-scoped interfaces for only the services a function needs (for example approved tool invocation, model access, cancellation, observability, and approved protocol/file/secret capabilities).
- [ ] Ensure future catalog/plugin-style functions do not receive unrestricted runtime objects, raw environment access, arbitrary filesystem access, raw secrets, or arbitrary subprocess access by default.
- [ ] Add deterministic tests proving the managed-function context exposes only the declared capabilities while existing `agent` / `llm` behavior remains compatible.
- [ ] Keep arbitrary in-process Python plugins outside the public product contract; document that the managed-function boundary is a programming/policy boundary, not hostile-code isolation.

#### B-005.7 — Complete sandbox lifecycle, retry, resume, and observability semantics

Current execution emits common task progress around sandbox start/finish and uses engine-neutral errors, but the planned sandbox-specific lifecycle metadata is not complete.

- [ ] Add common sandbox execution metadata to lifecycle/observability events where useful: execution ID, invocation/session/workflow/task identity, duration, terminal status, and sanitized error code. Do not expose PID, process-group IDs, or backend-native state as public contract.
- [ ] Make timeout, cancellation, policy rejection, invalid runtime/executable, resource/output limits, non-zero exit, startup failure, and cleanup failure consistently observable through the common error/event model without leaking command secrets or environment values.
- [ ] Define and test retry/resume behavior explicitly. Sandbox execution is not exactly-once; side-effecting commands require idempotency/deduplication discipline after ambiguous failure or restart.
- [ ] Persist only common recovery metadata if required by the proven retry/resume design; do not persist process-native identifiers as durable API state.

#### B-005.8 — Close deterministic security-negative coverage

The current tests already cover disabled-by-default policy, unsupported container/external scripts, environment filtering, approved secret references, timeout, total output limit, non-zero exit, workspace quota/cleanup, cancellation, and cross-engine `run.script` / `run.shell` parity. Add only the missing high-value cases.

- [ ] Add explicit process-tree/orphan termination tests, including a child/grandchild process that would survive if process-group cancellation were incorrect.
- [ ] Add inherited-file-descriptor negative coverage and invalid executable/runtime coverage at the execution boundary.
- [ ] Add supported resource-limit behavior tests (CPU/process/file-size/address-space where deterministic on the CI platform) and concurrent/failure cleanup tests.
- [ ] Prove runtime environment secrets are absent unless explicitly approved and that runtime-generated errors/logs do not echo approved secret values. Document explicitly that code intentionally given a secret can itself print/exfiltrate it; the internal backend is not a hostile-code security boundary.
- [ ] Keep filesystem claims honest: the internal backend provides an isolated working directory and cleanup, **not** kernel-enforced denial of arbitrary absolute-path reads. Test workspace/traversal behavior that is actually enforceable and keep `/v1/capabilities` explicit about the limitation rather than claiming container/VM-grade filesystem isolation.

#### B-005.9 — Add sandbox-enabled release-image acceptance

Current Docker acceptance proves both release images under arbitrary UID, read-only root filesystem, bounded `/tmp`, startup/health/invocation, restart/resume, metadata, size, and retained logs, but it does not execute sandbox tasks inside those hardened containers.

- [ ] Add deterministic sandbox-enabled container fixtures for both ADK and LangGraph images with `sandbox.enabled=true`; enable shell only in the specific shell fixture.
- [ ] Execute inline Python `run.script` and approved direct-executable `run.shell` through the normal API in both images and verify `/v1/capabilities` matches the configured policy.
- [ ] Run sandbox-enabled images as arbitrary UID with read-only root filesystem and bounded `/tmp`; verify no runtime/package installation occurs, temporary sandbox workspaces remain usable, and cleanup succeeds.
- [ ] Verify timeout/cancellation and graceful SIGTERM leave no sandbox child processes or execution workspaces behind.
- [ ] Retain container logs and prove runtime-generated logs/errors do not expose configured secret values.
- [ ] Keep production defaults fail-closed until this acceptance is green. After the final green run, record the CI run/artifacts in `PROJECT.md`, update this file, and mark B-005 complete.

**B-005 acceptance:** both engines route executable tasks through one framework-neutral `SandboxManager`; approved internal script/shell operations are bounded and policy-controlled; managed functions receive appropriately scoped runtime capabilities; lifecycle/error behavior is engine-neutral; deterministic negative tests cover enforceable internal controls; both hardened release images pass sandbox-enabled acceptance; and documentation/capabilities clearly state that the internal backend is not hard isolation.

### B-004 carry-over — External-catalog release acceptance (P3)

The external-catalog implementation itself is complete. Keep only the remaining release gates here rather than retaining the completed B-004 implementation checklist.

- [ ] Verify both independent release images with an explicitly configured approved external catalog under arbitrary UID, read-only root filesystem, bounded `/tmp`, no startup installation, and retained-log credential safety using a deterministic CI-controlled HTTPS fixture/trust setup.
- [ ] After that acceptance is green, record the verified CI run/artifacts in `PROJECT.md` and this file, then update the advertised external-catalog production capability/status consistently. Until then, keep the production acceptance claim fail-closed.

### B-006 — External sandbox backends (P2)

Depends on B-005 acceptance. External backends provide stronger isolation and must reuse the same common sandbox boundary instead of introducing engine-specific execution paths.

#### B-006.1 — Extend the backend-neutral external execution contract

- [ ] Extend/confirm the B-005 request/result/capability SPI for external-backend needs: image/runtime selection, approved input files, environment/secret references, resource requirements, network/isolation requirements, cancellation, bounded output, and cleanup without exposing infrastructure-native identifiers publicly.
- [ ] Keep backend selection deployment-controlled, not workflow-authoring syntax.
- [ ] Define policy compatibility and fail-closed behavior when the selected backend cannot provide a requested isolation capability.

#### B-006.2 — Docker backend

- [ ] Implement an optional Docker sandbox backend without giving the main runtime unrestricted `/var/run/docker.sock` access.
- [ ] Use a separate controller or restricted Docker API/socket proxy exposing only the minimum create/start/wait/log/stop/remove operations required for sandbox workloads.
- [ ] Enforce approved registries/images, non-root execution, no privileged mode, no host networking, no host mounts, bounded resources/output, timeout, cleanup, and secret isolation.
- [ ] Add deterministic local acceptance where Docker is available without making Docker a dependency of the internal sandbox/core test suite.

#### B-006.3 — Kubernetes/OpenShift backend

- [ ] Implement an optional backend that creates ephemeral Pods/Jobs in a dedicated sandbox namespace/project through a narrowly scoped ServiceAccount.
- [ ] Enforce non-root/arbitrary UID compatibility, no privileged mode, no host namespaces, no `hostPath`, bounded ephemeral storage, resource requests/limits, approved images, NetworkPolicy/egress restrictions, and cleanup/TTL behavior.
- [ ] Keep the runtime ServiceAccount unable to manage unrelated workloads or namespaces.
- [ ] Add OpenShift-focused acceptance for SCC/security-context compatibility and cleanup after timeout/cancellation/restart.

#### B-006.4 — Enable `run.container` only on container-capable backends

- [ ] Route `run.container` through `SandboxManager` only when the selected external backend advertises container execution.
- [ ] Continue rejecting `run.container` on the internal backend rather than silently emulating it.
- [ ] Add shared ADK/LangGraph contracts proving backend-independent observable semantics and explicit capability differences.

**B-006 acceptance:** Docker and/or Kubernetes/OpenShift can be selected as stronger sandbox backends without changing engine adapters; infrastructure permissions are narrowly scoped; direct unrestricted daemon/cluster access from the runtime is avoided; and `run.container` is advertised only where the selected backend safely supports it.

### B-007 — Optional A2A exposure and streaming evaluation (P3)

Depends on B-004 production acceptance and a separate capability decision. These features remain optional and must not become a default portability claim.

- [ ] Review relevant A2A and streaming protocol requirements and document the boundary between inbound exposure, outbound calls, lifecycle events, and engine-native streaming.
- [ ] Define authentication, authorization, delegation, rate limits, request/response limits, cancellation, backpressure, reconnection, and observability requirements before implementation.
- [ ] Decide whether each feature is engine-neutral, engine-specific, or intentionally deferred; update `/v1/capabilities` and the Project Definition accordingly.
- [ ] If approved, implement one bounded slice with deterministic tests and explicit security controls without claiming full ecosystem conformance.

### B-008 — Additional engine adapter (P3)

Depends on stable core contracts and completed cross-engine acceptance.

- [ ] Select an additional engine only after documenting concrete user value and dependency/licensing/runtime impact.
- [ ] Implement an independent package/image through the existing engine SPI; do not import engine-specific code into core or merge dependency graphs.
- [ ] Extend the same shared fixtures, expected outputs, persistence boundaries, capability reporting, Docker acceptance, and lock/release gates before advertising the engine.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Route executable workflow operations through the common sandbox service; engines must never create independent subprocess/Docker/Kubernetes execution paths.
- Treat the internal sandbox as a controlled execution boundary, not a hard isolation boundary; advertise only controls actually enforced by the selected platform/backend.
- Do not make Docker or Kubernetes a requirement for the internal sandbox milestone.
- Do not give the main runtime unrestricted Docker socket or cluster-wide Kubernetes access for external sandbox execution.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, schedule, sandbox execution, and engine-native state lifecycles.
- Do not require paid model/API access or install dependencies at container startup/runtime execution.
- Keep default production capabilities fail-closed until their deterministic contract and release-image acceptance gates are green.