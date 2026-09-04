# Open Workflow Agent Backlog

`Project Definition.md` is the architecture/product authority. `PROJECT.md` records verified implementation state. `AGENTS.md` defines repository rules. This file contains only active or intentionally deferred work.

## Current Phase

**v0.1.0 is released. Current `main` is unreleased pre-stable work focused on traffic policy and external interoperability evidence.**

The public product contract is still stabilizing. External A2A wire behavior targets the official A2A v1 definitions. Open Workflow 1.0.3 keeps its own schema-defined A2A call vocabulary; OWA translates that vocabulary to the selected A2A wire operation at the runtime protocol boundary rather than changing the Open Workflow schema.

## Current Implementation State

Verified implementation detail lives in `PROJECT.md` and is updated after every gate-passing change. Formal release remains `v0.1.0`; current `main` changes are unreleased pre-stable work.

## Active Backlog

### P0 — Security hardening

- [ ] **SECURITY-5** — wire OAuth2 client-credentials and mTLS security profile types into outbound protocol adapters (`protocols.py`) so workflows can call HTTPS/client-cert-protected endpoints using named profiles. Currently only `bearer` and `api_key` profiles inject HTTP headers; `oauth2_client_credentials` and `mtls` profiles are parsed and validated but not resolved by the outbound HTTP client.
- [ ] **SECURITY-7** — add an optional built-in API authentication layer for the main HTTP API (`/v1/*` endpoints). Today the main REST endpoints are unauthenticated; only A2A has optional bearer auth. Deployments without a reverse proxy or service mesh have no way to protect invoke/resume/cancel/approvals/schedules endpoints.

### P0 — Deployment readiness

- [ ] **DEPLOY-1** — add OpenShift-specific SCC/security-context/arbitrary-UID sandbox acceptance. Kubernetes acceptance is green on kind; OpenShift-specific enforcement is not validated until an OpenShift cluster is exercised.
- [ ] **DEPLOY-2** — publish multi-arch Docker images (`linux/amd64` + `linux/arm64`) for the runtime and sandbox controller images. Current images are single-architecture `linux/amd64` only.

### P1 — Observability

- [ ] **OBS-1** — add a Prometheus `/metrics` endpoint exposing invocation counts, latency histograms, active invocations, scheduler jobs, approval queue depth, sandbox execution stats, traffic policy counters, and A2A request/error rates. Currently the runtime has no machine-readable metrics surface.
- [ ] **OBS-2** — add structured JSON logging support. The runtime currently uses plain-text log formatting; structured logging is required for log aggregation platforms (ELK, Loki, CloudWatch).

### P1 — Dependency hygiene

- [ ] **DEPS-1** — move `numpy` and `pypdf` from core hard dependencies to an optional `[knowledge]` extra. These are only needed for the knowledge/embedding service but are currently installed for every deployment even when knowledge is unused. This reduces the base image footprint and install time.
- [ ] **DEPS-2** — fix ruff `target-version` from `"py311"` to `"py312"` in `pyproject.toml`. The project requires `python >= 3.12` but the linter targets `py311`, which disables 3.12-specific checks.
- [ ] **DEPS-3** — enable mypy for engine packages. Currently `mypy` in `pyproject.toml` excludes `^(engines|tests)/`; engine adapter code is not statically type-checked.

### P1 — Documentation

- [ ] **DOCS-1** — document the memory API (`/v1/memory` or equivalent add/search/delete endpoints). The memory service is implemented and tested but has no dedicated API documentation.
- [ ] **DOCS-2** — document the scheduling API (`/v1/schedules` endpoints for create/get/cancel). Scheduling is implemented and tested but only lightly referenced in `docs/api.md`.
- [ ] **DOCS-3** — document the approvals API (`/v1/approvals` endpoints for list/get/decide). Approvals are implemented and tested but lack a dedicated guide.
- [ ] **DOCS-4** — document the events API (`/v1/events` for publish, `/v1/events/lifecycle` for snapshot, `/v1/events/lifecycle/stream` for SSE). Events are implemented but lack a dedicated guide.
- [ ] **DOCS-5** — publish a machine-readable OpenAPI specification for the HTTP API. FastAPI auto-generates `/docs` and `/openapi.json` but these are not versioned, documented, or tested for stability.
- [ ] **DOCS-6** — create an architecture overview document for new contributors. `Project Definition.md` is 1200+ lines; a concise architecture overview covering the runtime pipeline, module structure, and extension points would lower the onboarding barrier.
- [ ] **DOCS-7** — document how to author custom catalog functions. `runtime-catalog/` contains `agent:1.0.0` and `llm:1.0.0` functions but there is no guide for creating new catalog entries.
- [ ] **DOCS-8** — update `docs/a2a-streaming-evaluation.md` to reflect that A2A streaming is now implemented. The document currently reads as an evaluation/proposal rather than an implementation record.

### P1 — Testing

- [ ] **TEST-1** — expand the Open Workflow CTK subset. Currently only 8 feature files are exercised (`branch`, `do`, `for`, `fork`, `raise`, `set`, `switch`, `wait`). Missing: `emit`, `listen`, `call` (HTTP/MCP/A2A), `flow`, and other CTK features that are or claim to be implemented.
- [ ] **TEST-2** — add performance benchmarks for the core runtime pipeline (workflow compilation, invocation latency, concurrency throughput). No benchmarking infrastructure exists today.
- [ ] **TEST-3** — increase the core coverage threshold from 80% to 90%. For a security-sensitive runtime that handles secrets, authorization, and external protocol traffic, 80% coverage is low.

### P2 — Kubernetes deployment

- [ ] **K8S-1** — create a Helm chart for Kubernetes deployment. Current `deploy/kubernetes/runtime.yaml` is a reference manifest, not a reusable chart. A Helm chart would simplify configuration, secret management, and upgrades.
- [ ] **K8S-2** — add Ingress/Gateway API resource templates to the deployment manifests. The current runtime.yaml exposes a ClusterIP Service but has no Ingress or HTTPRoute.
- [ ] **K8S-3** — add NetworkPolicy for the runtime namespace. Only the sandbox boundary namespace has NetworkPolicy enforcement; the runtime namespace itself is unrestricted.
- [ ] **K8S-4** — add monitoring/alerting rule templates (Prometheus ServiceMonitor, alerting rules for error rates, latency, and sandbox failures).

### P2 — Architecture

- [ ] **ARCH-1** — add dependency health checks to `/health/ready`. Currently `/health/ready` returns `ok` when the lifespan completes but does not verify downstream dependencies (database connectivity, model availability, knowledge index readiness).
- [ ] **ARCH-2** — add CORS configuration support. The API has no CORS restrictions; add a configurable CORS policy for browser-based clients while keeping the default restrictive.
- [ ] **ARCH-3** — add per-endpoint or per-principal rate limiting to the traffic policy. The current traffic policy is global; A2A endpoints may need different limits than REST endpoints, and authenticated principals may deserve higher quotas.

## Intentionally Deferred

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

### AsyncAPI implementation

AsyncAPI 3.1.0 is pinned as a future binding baseline but is not implemented. No active work planned.

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
