# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise active execution backlog; completed work is recorded in project status and git history.

## Current Phase

**B-003 — Deferred workflow lifecycle features.** Bounded eventing, lifecycle CloudEvents, scheduling, local sub-workflows, and bounded durable HITL approvals are complete.

## Active Backlog — Ordered

### B-004 — Secure external catalog resolution (P3)

External catalogs must be deployment-controlled, fail closed, and portable across ADK and LangGraph. Do not weaken the existing local-catalog boundary or expose credentials, remote catalog contents, or engine-native state through the public API.

#### B-004.1 — Define the external-catalog contract and threat model

- [ ] Document the supported Open Workflow 1.0.3 `use.catalogs` shape and the mapping from a workflow catalog alias to a deployment-approved catalog definition.
- [ ] Define supported catalog response contents, function/workflow identity, version selection, digest/signature policy, collision behavior, and error semantics.
- [ ] Define the trust model and threat cases: SSRF, private/link-local address access, DNS rebinding, redirect escape, oversized responses, malicious catalog content, credential leakage, stale cache use, and catalog substitution.
- [ ] Define the capability contract for enabled, rejected, unavailable, unverified, and stale catalogs without claiming ecosystem-wide Open Workflow conformance.

#### B-004.2 — Add deployment-controlled trust configuration

- [ ] Add strict runtime configuration for approved catalog aliases/endpoints, allowed schemes/hosts, TLS verification, authentication references, request limits, cache policy, and integrity/version pins.
- [ ] Keep secrets outside workflow files; resolve credentials only through deployment-provided secret/environment references and never serialize or log them.
- [ ] Reject unknown catalog configuration, ambiguous aliases, insecure overrides, missing pins where the selected policy requires them, and workflow references not explicitly approved by deployment configuration.
- [ ] Preserve backward compatibility: local `workflow.catalog` continues to work without network access, while external catalogs remain disabled unless explicitly configured.

#### B-004.3 — Implement a dedicated secure catalog transport and resolver

- [ ] Implement a core-only resolver with bounded connect/read/write/pool timeouts, response-size enforcement before unbounded parsing, TLS verification by default, and redirects disabled by default.
- [ ] Enforce host allowlists and validate every redirect target; prevent loopback, private, link-local, multicast, reserved, and other disallowed network destinations, including DNS-rebinding paths.
- [ ] Use the configured authentication abstraction without accepting arbitrary credential material from workflow definitions; prevent authorization headers from crossing hosts or appearing in errors/logs.
- [ ] Translate transport, parse, trust, pin, and availability failures into the common error contract with safe, non-sensitive details.

#### B-004.4 — Integrate catalog loading with the workflow pipeline

- [ ] Resolve approved catalogs before plan construction, then apply the existing `official schema validation → capability validation → normalization → plan` pipeline to every loaded definition.
- [ ] Keep the canonical execution plan internal and immutable; do not introduce a public catalog DSL or a second workflow language.
- [ ] Define deterministic precedence and collision rules for built-in, local, and external catalog entries; external content must not silently replace built-in `agent` or `llm` functions.
- [ ] Fail closed on missing catalogs, unsupported definitions, identity/version mismatches, invalid signatures/digests, and ambiguous resolution; never silently fall back to stale or unverified content.
- [ ] Keep core framework-neutral and preserve separate engine-owned state and engine adapters.

#### B-004.5 — Add bounded cache and revalidation behavior

- [ ] Add an isolated catalog cache with bounded size/age and explicit metadata for endpoint, identity, version, digest, validation result, ETag/Last-Modified, and retrieval time.
- [ ] Implement conditional revalidation where supported and define cold-start, unchanged, changed, expired, unavailable, and pin-mismatch behavior.
- [ ] Ensure a stale, unverified, or integrity-mismatched entry cannot be used unless an explicit future policy permits it; the initial policy must fail closed.
- [ ] Keep catalog cache lifecycle and namespace separate from invocation, memory, knowledge, approval, schedule, and engine-native persistence.

#### B-004.6 — Expose capabilities and operational behavior

- [ ] Report external-catalog capability state, trust policy summary, and supported verification mode through `/v1/capabilities` without exposing endpoints, secrets, or remote catalog contents unnecessarily.
- [ ] Add safe structured observability for catalog fetch, validation, cache, and rejection outcomes using correlation identifiers and sanitized error codes.
- [ ] Ensure startup/readiness behavior is deterministic when an enabled catalog is unavailable; do not report ready with a workflow that was not fully verified.
- [ ] Document configuration, secret injection, network egress requirements, rotation/pin updates, cache behavior, and rollback to local catalogs.

#### B-004.7 — Prove cross-engine portability and security

- [ ] Add deterministic resolver unit tests using mock transports; no test may require a public network, paid provider, or live external catalog.
- [ ] Cover invalid schemes, disallowed hosts and IP ranges, DNS/rebinding defenses, TLS-verification policy, redirect escape, timeout, response-size, malformed payload, authentication isolation, digest/signature mismatch, version mismatch, cache/revalidation, and fail-closed behavior.
- [ ] Add shared contract fixtures where an approved external catalog contributes a supported definition, and verify identical observable results on ADK and LangGraph.
- [ ] Run the existing shared contract suite after every engine change; add CTK coverage only for scenarios supported by the implemented Portable Profile and preserve pinned provenance.
- [ ] Add API and readiness tests proving rejected/unavailable catalogs do not execute partially compiled workflows or leak sensitive details.

#### B-004.8 — Complete CI, container, and release acceptance

- [ ] Add a deterministic local catalog test server/transport to CI; do not permit CI to depend on public endpoints.
- [ ] Verify both independent images with external catalog configuration, arbitrary UID, read-only root, bounded `/tmp`, no startup installation, and no credential leakage in retained logs.
- [ ] Verify image metadata, exact locks, package contents, image-size limits, root quality gates, ADK/LangGraph native tests, shared contracts, and applicable CTK results.
- [ ] Update end-user, configuration, deployment, API, and development documentation with secure examples and explicit unsupported cases.
- [ ] Keep `use.catalogs` rejected in production images until all B-004 acceptance checks are green; record the verified CI run and artifacts in `PROJECT.md` and this file.

**B-004 acceptance:** an explicitly approved external catalog can be fetched, verified, validated, cached/revalidated, and resolved through the common core with bounded secure behavior; both engines produce equivalent contract results; failures are fail-closed and non-sensitive; CI and both container images pass all relevant gates. Only then may `use.catalogs` be enabled in the advertised capability profile.

### B-005 — Optional A2A exposure and streaming evaluation (P3)

Depends on B-004 and a separate capability decision. These features must remain optional and must not become a default portability claim.

- [ ] Review the relevant A2A and streaming protocol requirements and document the boundary between inbound exposure, outbound calls, lifecycle events, and engine-native streaming.
- [ ] Define authentication, authorization, delegation, rate limits, request/response limits, cancellation, backpressure, reconnection, and observability requirements before implementation.
- [ ] Decide whether each feature is engine-neutral, engine-specific, or intentionally deferred; update `/v1/capabilities` and the Project Definition accordingly.
- [ ] If approved, implement one bounded slice with deterministic tests, explicit security controls, and no claim of full A2A/Open Workflow ecosystem conformance.

### B-006 — Additional engine adapter (P3)

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

External catalogs remain explicitly disabled. Generic workflow event delivery remains process-local/non-durable; durability is currently provided only for the bounded approval contract and scheduler state.

Custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling remain out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, and schedule lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
- Do not advertise external catalog, A2A exposure, streaming, or additional-engine portability before deterministic contracts and capabilities prove it.
