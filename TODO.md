# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise active execution backlog; completed work is recorded in project status and git history.

## Current Phase

**B-004 — Secure external catalog resolution (in progress).** B-003 bounded eventing, lifecycle CloudEvents, scheduling, local sub-workflows, and durable HITL approvals remain complete. External catalog support is implemented behind explicit deployment trust, with final container/CI acceptance still pending.

**Next planned phase:** **B-005 — Internal sandbox execution foundation.** It must be completed before enabling `run.script` or `run.shell`, and before any Docker/Kubernetes/OpenShift execution backend is implemented.

## Active Backlog — Ordered

### B-004 — Secure external catalog resolution (P3)

External catalogs must be deployment-controlled, fail closed, and portable across ADK and LangGraph. Do not weaken the existing local-catalog boundary or expose credentials, remote catalog contents, or engine-native state through the public API.

#### B-004.1 — Define the external-catalog contract and threat model

- [x] Document the supported Open Workflow 1.0.3 `use.catalogs` shape and the mapping from a workflow catalog alias to a deployment-approved catalog definition.
- [x] Define supported declarative function contents, path identity, exact semantic-version selection, optional SHA-256 pin policy, built-in collision behavior, and common error semantics. External workflow definitions and remote scripts remain unsupported.
- [x] Define the trust model and threat cases: SSRF, private/link-local address access, redirect escape, oversized responses, malicious catalog content, credential leakage, stale cache use, and catalog substitution. DNS destinations are resolved and validated immediately before production requests, then pinned at connection level.
- [x] Define the capability contract for configured, validated, resolved, cached, rejected, and unavailable catalogs without claiming ecosystem-wide Open Workflow conformance.

#### B-004.2 — Add deployment-controlled trust configuration

- [x] Add strict runtime configuration for approved catalog aliases/endpoints, HTTPS-only host allowlists, mandatory TLS verification, authentication references, request limits, cache policy, and integrity/version pins.
- [x] Keep secrets outside workflow files; resolve credentials only through deployment-provided secret/environment references and never serialize or log them.
- [x] Reject unknown catalog configuration, duplicate aliases/hosts, insecure overrides, missing pins where the selected policy requires them, and workflow references not explicitly approved by deployment configuration.
- [x] Preserve backward compatibility: local `workflow.catalog` continues to work without network access, while external catalogs remain disabled unless explicitly configured.

#### B-004.3 — Implement a dedicated secure catalog transport and resolver

- [x] Implement a core-only resolver with bounded connect/read/write/pool timeouts, response-size enforcement before unbounded parsing, TLS verification by default, and redirects disabled by default.
- [x] Enforce host allowlists and reject redirects by default; prevent loopback, private, link-local, multicast, reserved, and other disallowed network destinations, including DNS-rebinding resistance. Production requests pin the validated public address set at the connection boundary while retaining the original hostname for TLS verification.
- [x] Use the configured authentication abstraction without accepting arbitrary credential material from workflow definitions; bind deployment credentials to the catalog resource host and prevent authorization headers from crossing hosts or appearing in errors/logs.
- [x] Translate transport, parse, trust, pin, and availability failures into the common error contract with safe, non-sensitive details.

#### B-004.4 — Integrate catalog loading with the workflow pipeline

- [x] Resolve approved catalogs before plan construction, then apply the existing `official schema validation → capability validation → normalization → plan` pipeline to every loaded definition. Startup, local child-workflow registration, and scheduled execution now use the same ordering before readiness or execution.
- [x] Keep the canonical execution plan internal and immutable; do not introduce a public catalog DSL or a second workflow language.
- [x] Define deterministic precedence and collision rules for built-in, local, and external catalog entries; external aliases cannot shadow the built-in `default` catalog.
- [x] Fail closed on missing catalogs, unsupported definitions, invalid pins/digests, and ambiguous resolution; stale or unverified content is never used.
- [x] Keep core framework-neutral and preserve separate engine-owned state and engine adapters.

#### B-004.5 — Add bounded cache and revalidation behavior

- [x] Add an isolated in-memory catalog cache with bounded size/age and metadata for resource URI, function identity, version, digest, validation result, ETag/Last-Modified, and retrieval time.
- [x] Implement conditional revalidation where supported and define cold-start, unchanged, changed, expired, unavailable, and pin-mismatch behavior.
- [x] Ensure a stale, unverified, or integrity-mismatched entry cannot be used unless an explicit future policy permits it; the initial policy fails closed.
- [x] Keep catalog cache lifecycle and namespace separate from invocation, memory, knowledge, approval, schedule, and engine-native persistence.

#### B-004.6 — Expose capabilities and operational behavior

- [x] Report external-catalog capability state, trust policy summary, and supported verification mode through `/v1/capabilities` without exposing endpoints, secrets, or remote catalog contents unnecessarily.
- [x] Add safe structured observability for catalog fetch, revalidation, cache, and rejection outcomes using correlation identifiers and sanitized error codes.
- [x] Ensure startup/readiness behavior is deterministic when an enabled catalog is unavailable; the application does not yield readiness with a workflow that was not fully resolved.
- [x] Document configuration, secret injection, network egress requirements, rotation/pin updates, cache behavior, and rollback to local catalogs.

#### B-004.7 — Prove cross-engine portability and security

- [x] Add deterministic resolver unit tests using mock transports; no test may require a public network, paid provider, or live external catalog.
- [x] Cover invalid schemes, disallowed hosts and IP ranges, DNS/rebinding defenses, TLS-verification policy, redirect escape, timeout, response-size, malformed payload, authentication isolation, digest/signature mismatch, version mismatch, cache/revalidation, and fail-closed behavior. Deterministic tests cover private DNS results and the connection-level pinned backend; the full local suite passes without public network access.
- [x] Add shared contract fixtures where an approved external catalog contributes a supported definition, and verify identical observable results on ADK and LangGraph.
- [x] Run the existing shared contract suite after every engine change; add CTK coverage only for scenarios supported by the implemented Portable Profile and preserve pinned provenance.
- [x] Add API and readiness tests proving rejected/unavailable catalogs do not execute partially compiled workflows or leak sensitive details.

#### B-004.8 — Complete CI, container, and release acceptance

- [x] Add a deterministic local catalog test server/transport to CI; the resolver suite uses `httpx.MockTransport` and requires no public endpoint.
- [ ] Verify both independent images with external catalog configuration, arbitrary UID, read-only root, bounded `/tmp`, no startup installation, and no credential leakage in retained logs.
- [x] Verify image metadata, exact locks, package contents, image-size limits, root quality gates, ADK/LangGraph native tests, shared contracts, and applicable CTK results. GitHub Actions run `32945536005` passed every job for commit `9c9dfa0edbb201a68947dc95fbd8860791cb6a49`.
- [x] Update end-user, configuration, deployment, API, and development documentation with secure examples and explicit unsupported cases.
- [ ] Keep `use.catalogs` rejected in production images until all B-004 acceptance checks are green; record the verified CI run and artifacts in `PROJECT.md` and this file.

**B-004 acceptance:** an explicitly approved external catalog can be fetched, verified, validated, cached/revalidated, and resolved through the common core with bounded secure behavior; both engines produce equivalent contract results; failures are fail-closed and non-sensitive; CI and both container images pass all relevant gates. Only then may `use.catalogs` be enabled in the advertised capability profile.

### B-005 — Internal sandbox execution foundation (P1)

This is the prerequisite for executable Open Workflow operations. The first sandbox must run inside the normal Open Workflow Agent deployment and must not require a Docker Engine, Kubernetes, or OpenShift. The internal sandbox is a controlled execution boundary, not a hard security boundary.

Architecture requirements are documented in `docs/sandbox-execution.md`.

#### B-005.1 — Define the sandbox contract and threat model

- [ ] Define `SandboxManager`, `SandboxBackend`, `SandboxExecutionRequest`, `SandboxExecutionResult`, cancellation, capability, and engine-neutral error contracts in core.
- [ ] Define the distinction between managed runtime functions, internal child-process execution, and later external isolation backends.
- [ ] Document threats including command injection, environment/secret leakage, path traversal, inherited file descriptors, runaway process trees, fork/process bombs, CPU/memory/disk exhaustion, oversized output, timeout escape, workspace leakage, network access, retry/resume duplication, and cleanup failure.
- [ ] State explicitly that an internal subprocess shares the runtime container/host kernel and is not equivalent to a container, pod, VM, or microVM isolation boundary.
- [ ] Preserve Open Workflow task references and keep backend/process-native identifiers out of the public API contract.

#### B-005.2 — Add strict sandbox policy and capability models

- [ ] Add strict typed configuration only for implemented internal policies: backend selection, timeout, input/output limits, environment policy, workspace policy, supported runtimes, and enforceable resource controls.
- [ ] Reject unknown/insecure configuration and do not expose a configuration switch for controls the implementation cannot actually enforce.
- [ ] Add sanitized `/v1/capabilities` reporting for internal process execution, supported script runtimes, shell support, cancellation, resource-limit enforcement, filesystem isolation level, and network isolation level.
- [ ] Keep sandbox policy common across ADK and LangGraph; engines must not own their own execution configuration.

#### B-005.3 — Introduce a managed function capability boundary

- [ ] Keep built-in `agent`, `llm`, and bounded protocol functions as managed runtime functions rather than converting them into child processes.
- [ ] Define the minimal capability-scoped execution context available to managed/executable functions: input, cancellation, approved workspace/files, approved protocol/network services, approved secret references, and observability context.
- [ ] Prevent future executable/plugin-style functions from receiving unrestricted runtime objects, environment access, filesystem access, secrets, or arbitrary subprocess access by default.
- [ ] Keep arbitrary in-process Python plugins outside the current public product contract.

#### B-005.4 — Implement `InternalSandboxBackend`

- [ ] Add a core-only internal backend that starts controlled OS child processes without Docker/Kubernetes dependencies.
- [ ] Prefer direct executable/argument-vector execution rather than implementation-created shell interpolation where Open Workflow semantics allow it.
- [ ] Create one execution-scoped temporary workspace per process and do not use the runtime working directory as the child workspace.
- [ ] Do not inherit the full runtime environment; allow only deployment-approved variables/values and explicit secret references.
- [ ] Close inherited file descriptors except those explicitly required and sanitize process startup state.
- [ ] Bound stdin, stdout, stderr, total output, execution time, and workspace usage where enforceable.
- [ ] Apply supported OS resource limits for CPU/process/file-size/address-space usage where practical and capability-advertise platform differences.
- [ ] Implement cancellation and terminate the child process tree/process group where the supported platform permits it.
- [ ] Guarantee best-effort workspace/process cleanup after success, failure, timeout, cancellation, or runtime shutdown.

#### B-005.5 — Integrate internal script execution

- [ ] Add a `RunPlan`/common run executor path that delegates `run.script` to `SandboxManager`; neither engine may call subprocess APIs directly.
- [ ] Define the initial supported script runtime set explicitly and package required interpreters in the release image; no dynamic package/runtime installation is allowed at startup or execution time.
- [ ] Preserve official Open Workflow script semantics without introducing another script DSL or templating language.
- [ ] Reject unsupported runtimes, dependency-install requests, host-path mounts, and backend-specific execution fields.

#### B-005.6 — Integrate internal shell execution

- [ ] Add `run.shell` only after the internal backend and script slice are proven.
- [ ] Keep shell execution separately capability-gated because it has a larger injection/expansion surface than direct executable invocation.
- [ ] Do not concatenate workflow/user data into an implementation-created command string; preserve Open Workflow-defined command semantics and fail closed on unsupported behavior.
- [ ] Apply the same timeout, cancellation, environment, output, resource, workspace, observability, and cleanup policies as script execution.

#### B-005.7 — Integrate lifecycle, retry, resume, and observability

- [ ] Emit common execution lifecycle events keyed by `invocation_id`, `session_id`, workflow identity, Open Workflow task reference, execution ID, duration, status, and sanitized error code.
- [ ] Translate timeout, cancellation, policy rejection, invalid runtime/executable, resource limit, output limit, non-zero exit, startup failure, and cleanup failure into the common error contract.
- [ ] Define retry/resume behavior explicitly: sandbox execution is not exactly-once and side-effecting commands require idempotency/deduplication discipline.
- [ ] Persist only common lifecycle/recovery metadata where required; do not make PID/process-group/backend-native state a stable application contract.

#### B-005.8 — Prove security and cross-engine portability

- [ ] Add deterministic core tests for timeout, cancellation, process-tree termination, environment filtering, secret non-leakage, workspace isolation/cleanup, invalid executables/runtimes, non-zero exit, output limits, and resource limits where supported.
- [ ] Add negative tests for attempts to read unapproved environment secrets, inherit runtime file descriptors, access disallowed runtime paths, escape the workspace through path tricks, and leave orphan child processes.
- [ ] Add shared `run.script` and `run.shell` contract fixtures and verify identical observable ADK/LangGraph results.
- [ ] Verify arbitrary UID, read-only root filesystem, bounded `/tmp`, no startup installation, graceful SIGTERM, and retained-log secret safety in both release images.
- [ ] Ensure the internal milestone test suite requires no Docker daemon, Kubernetes cluster, public network, or paid API.
- [ ] Keep `run.script` and `run.shell` rejected in production capabilities until all relevant B-005 acceptance checks are green.

**B-005 acceptance:** both engines route executable tasks through one framework-neutral `SandboxManager`; the internal backend executes approved script/shell operations with bounded process, environment, workspace, output, timeout, cancellation, cleanup, and observability behavior; security limitations are explicit; deterministic contracts prove parity; and Docker/Kubernetes are not required.

### B-006 — External sandbox backends (P2)

Depends on B-005. External backends provide stronger infrastructure isolation and must reuse the same sandbox request/result/capability contract rather than creating engine-specific execution paths.

#### B-006.1 — Backend-neutral external execution contract

- [ ] Confirm the B-005 SPI can represent image/runtime selection, files, environment references, resource limits, network/isolation requirements, cancellation, bounded output, and cleanup without exposing infrastructure-native identifiers publicly.
- [ ] Define backend selection as deployment configuration, not workflow authoring syntax.
- [ ] Define policy compatibility/fail-closed behavior when a workflow requires an isolation capability the selected backend cannot provide.

#### B-006.2 — Docker backend

- [ ] Implement an optional Docker sandbox backend without giving the Open Workflow Agent runtime unrestricted `/var/run/docker.sock` access.
- [ ] Use a separate controller or restricted Docker API/socket proxy exposing only the minimum create/start/wait/log/stop/remove operations required for sandbox workloads.
- [ ] Enforce approved registries/images, non-root execution, no privileged mode, no host networking, no host mounts, bounded resources/output, timeout, cleanup, and secret isolation.
- [ ] Add deterministic local acceptance where Docker is available without making Docker a dependency of the internal sandbox/core test suite.

#### B-006.3 — Kubernetes/OpenShift backend

- [ ] Implement an optional backend that creates ephemeral Pods/Jobs in a dedicated sandbox namespace/project through a narrowly scoped ServiceAccount.
- [ ] Enforce non-root/arbitrary UID compatibility, no privileged mode, no host namespaces, no hostPath mounts, bounded ephemeral storage, resource requests/limits, approved images, NetworkPolicy/egress restrictions, and cleanup/TTL behavior.
- [ ] Keep the runtime ServiceAccount unable to manage unrelated workloads/namespaces.
- [ ] Add OpenShift-focused acceptance for SCC/security-context compatibility and cleanup after timeout/cancellation/restart.

#### B-006.4 — Enable container execution only on container-capable backends

- [ ] Route `run.container` through `SandboxManager` only when the selected external backend advertises container execution.
- [ ] Reject container execution on the internal backend rather than silently emulating it.
- [ ] Add shared ADK/LangGraph contracts proving backend-independent observable semantics and explicit capability differences.

**B-006 acceptance:** Docker and/or Kubernetes/OpenShift can be selected as stronger sandbox backends without changing workflow definitions or engine adapters; infrastructure permissions are narrowly scoped; direct unrestricted daemon/cluster access from the runtime is avoided; and `run.container` is advertised only where the selected backend safely supports it.

### B-007 — Optional A2A exposure and streaming evaluation (P3)

Depends on B-004 and a separate capability decision. These features must remain optional and must not become a default portability claim.

- [ ] Review the relevant A2A and streaming protocol requirements and document the boundary between inbound exposure, outbound calls, lifecycle events, and engine-native streaming.
- [ ] Define authentication, authorization, delegation, rate limits, request/response limits, cancellation, backpressure, reconnection, and observability requirements before implementation.
- [ ] Decide whether each feature is engine-neutral, engine-specific, or intentionally deferred; update `/v1/capabilities` and the Project Definition accordingly.
- [ ] If approved, implement one bounded slice with deterministic tests, explicit security controls, and no claim of full A2A/Open Workflow ecosystem conformance.

### B-008 — Additional engine adapter (P3)

Depends on stable core contracts and completed cross-engine acceptance.

- [ ] Select an additional engine only after documenting the concrete user value and dependency/licensing/runtime impact.
- [ ] Implement an independent package/image through the existing engine SPI; do not import engine-specific code into core or merge dependency graphs.
- [ ] Extend the same shared fixtures, expected outputs, persistence boundaries, capability reporting, Docker acceptance, and lock/release gates before advertising the engine.

## Completed B-003 slices

- **Bounded eventing:** `emit`, `listen` with the `one` strategy, and `POST /v1/events` use a process-local non-durable bus. `all`, `any`, `foreach`, general replay, and durable broker delivery remain unsupported.
- **Lifecycle CloudEvents:** `GET /v1/events/lifecycle` exposes bounded CloudEvents 1.0 JSON snapshots without engine-native state; it is non-streaming and non-durable.
- **Durable scheduling:** `schedule.after` and `schedule.every` use persisted leases and restart reclaim with at-least-once execution. `cron`, event-triggered `on`, distributed ownership, and scheduler streaming remain unsupported.
- **Local sub-workflows:** the Open Workflow `run` task resolves deployment-configured local workflow definitions from `workflow.catalog`; child invocations retain separate common metadata and engine-owned execution state. Shell/script execution remains disabled.
- **Bounded durable HITL:** approval requests and decisions compose with standard `emit`/`listen` events rather than introducing a proprietary workflow task. Approval state is persisted in an isolated common store for SQLite/PostgreSQL, decisions are operator-authorized and idempotent, approval inbox reads are protected, and persisted decisions replay through the normal `listen` path after restart. Shared ADK/LangGraph contracts verify equivalent replay behavior. The current authorization boundary is a deployment-configured bearer secret plus operator identity header; it is intentionally not a full identity-management system.

External catalogs are opt-in behind the in-progress B-004 resolver and are not yet part of the advertised production acceptance profile. Generic workflow event delivery remains process-local/non-durable; durability is currently provided only for the bounded approval contract and scheduler state.

Custom inbound A2A exposure, visual designers, BPMN, executable shell/script/container tasks, and distributed scheduling remain out of the advertised production profile unless their planned acceptance gates are completed.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Route future executable workflow operations through the common sandbox service; engines must never create independent subprocess/Docker/Kubernetes execution paths.
- Treat the internal sandbox as a controlled execution boundary, not a hard isolation boundary; advertise only controls actually enforced on the current platform/backend.
- Do not make Docker or Kubernetes a requirement for the internal sandbox milestone.
- Do not give the main runtime unrestricted Docker socket or cluster-wide Kubernetes access for external sandbox execution.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, schedule, sandbox execution, and engine-native state lifecycles.
- Do not require paid model/API access or install dependencies at container startup/runtime execution.
- Do not advertise external catalog, executable sandbox tasks, A2A exposure, streaming, or additional-engine portability before deterministic contracts and capabilities prove it.
