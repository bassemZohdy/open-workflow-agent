# Changelog

All notable changes to the Open Workflow Agent are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Bounded inbound A2A stable-v1 profile behind deployment configuration:
  - Agent Card discovery at `/.well-known/agent-card.json`;
  - JSON-RPC `SendMessage`;
  - HTTP+JSON `/message:send`;
  - selectable `jsonrpc` (default) and `http_json` transports;
  - deployment-configured public base URL;
  - optional bearer authentication via a named security profile;
  - message/request bounds and sanitized transport-specific errors.
- Stable protocol baseline record covering Open Workflow `1.0.3`, A2A `1.0.1`, MCP `2026-07-28`, OpenAPI `3.2.0`, CloudEvents `1.0.2`, and AsyncAPI `3.1.0`.
- Protocol/security architecture decisions for named reusable security profiles, standard authorization vocabulary, A2A skill ownership, A2A Task projection, traffic-policy separation, and explicit enterprise identity boundaries.
- Deterministic stable-protocol client tests for the current MCP/A2A common-client migration.
- `RuntimeConfig.security.profiles`: a strict-parsed, named security profile section (`bearer`, `api_key`, `oauth2_client_credentials`, `mtls`) exposed through the main runtime YAML plus `OWA__SECURITY__...` overrides.

### Changed

- A2A v0.3 discovery/method/Part compatibility assumptions were removed. The bounded inbound profile now targets A2A release `1.0.1` and advertises protocol version `1.0` only for implemented behavior.
- The active A2A roadmap now treats persistent A2A Tasks as a projection over common OWA invocation/`ExecutionHandle` state rather than a second workflow or persistence engine.
- A2A streaming/resubscription is ordered after Task state and will reuse common lifecycle/event infrastructure without exposing engine-native checkpoint/stream objects.
- Authentication/authorization is moving from temporary protocol-specific credential fields to reusable named deployment security profiles. A2A inbound bearer authentication is the first adapter wired to `security.profiles` (`a2a.security_profile` replaces `auth_token`); outbound protocol clients and the approvals operator check remain on temporary fields. Initial supported mechanisms are `bearer`, `api_key`, `oauth2_client_credentials`, and `mtls`.
- Rate limiting, concurrency, burst/admission, and future circuit policy are explicitly separated into deployment traffic policy rather than being folded into identity/security configuration.
- `run.container` admits the deployment-enabled Kubernetes sandbox backend with its own exact-digest allowlist.
- Kubernetes sandbox Jobs run as numeric non-root `65532:65532`; deadline failures surface `sandbox_timeout`; runtime execution payloads use an explicit JSON content type.

### Fixed

- The A2A Agent Card uses deployment-configured `a2a.public_base_url` rather than deriving public identity from the inbound request, which is incorrect behind reverse proxies.
- Runtime versioning is single-sourced and checked against package versions in CI/release workflows.
- Project/backlog/A2A documentation now reflects the shipped bounded A2A v1 boundary instead of the older state where inbound A2A was still described as deferred.

### Current deferred scope

- Persistent A2A Tasks, task get/cancel, input-required/resume mapping, protocol-native async behavior, and streaming/resubscription remain active backlog rather than shipped capability.
- A2A push notifications remain intentionally deferred until an outbound callback trust/security model exists.
- A broad/full A2A conformance claim remains deferred until Task/streaming/interoperability gates are green.
- OpenShift-specific sandbox acceptance remains deferred until an OpenShift cluster is available.
- Microsoft Agent Framework remains an optional CI-covered adapter, not a production image/release target.
- Multi-tenancy and delegated-user identity/token exchange/consent inside OWA remain out of current scope.

## [0.1.0] - 2026-08-28

First formal release: published from the verified `main` head with exact-version, minor-series, `latest`, and source-SHA image tags on GHCR and Docker Hub.

### Added

- Configuration-driven Open Workflow 1.0.3 runtime with interchangeable ADK and LangGraph engines, portable execution plans derived internally, and a generated default workflow for every invocation.
- Knowledge indexing/retrieval with local FastEmbed/ONNX embeddings, memory, session, checkpoint, schedule, and durable approval state behind common stores with SQLite reference and PostgreSQL extras.
- Durable human-in-the-loop approvals: request/decision state persisted separately, bearer-authorized and idempotent operator decisions, protected inbox reads, and replay through standard `listen` semantics after restart.
- Scheduler with `schedule.after`/`schedule.every` persistence, restart reclaim, and at-least-once semantics; local sub-workflows through deployment-configured `workflow.catalog`.
- Bounded lifecycle CloudEvents 1.0 JSON snapshots and a bounded lifecycle SSE endpoint advertised through `features.lifecycleStreaming`.
- Secure external catalog resolution: deployment-controlled alias/host/endpoint policy, HTTPS/TLS enforcement, bounded streaming fetches, no redirects, environment-only authentication, semantic-version references, optional/required SHA-256 pins, isolated cache/revalidation, and connection-level DNS-rebinding resistance.
- Internal sandbox for executable workflow operations as a controlled execution boundary (dedicated workspace, bounded environment/output/time), shared by both engines through the common `SandboxManager` contract.
- External sandbox backends: deployment-selected Docker backend with a restricted Unix-socket controller, approved-image policy, and fail-closed `run.container`; Kubernetes/OpenShift controller boundary with deployment-owned namespace/ServiceAccount/network-policy controls.
- Bounded protocol clients (HTTP/MCP/A2A/OpenAPI) with timeouts, TLS verification, response-size limits, redirect policy, and authentication abstraction.
- Microsoft Agent Framework native adapter as an optional third engine package (not yet a production image/release target).
- Restricted Docker and Kubernetes sandbox-controller packages with digest-pinned images published through the release pipeline (Trivy-scanned, provenance-attested) and a `compose.sandbox.yaml` reference wiring.
- Knowledge manifest entries record an index timestamp, real parser identity, and chunking algorithm identity, with automatic migration for existing databases.
- Reference Kubernetes/OpenShift runtime manifests under `deploy/`, validated in CI with kubeconform; HEALTHCHECK/STOPSIGNAL on runtime images and Compose services.
- Continuous image publication to GHCR and Docker Hub from green `main`, with exact-version/minor-series/`latest`/source-SHA tags on tagged releases plus OCI SBOM/provenance metadata and GHCR build-provenance attestations.
- Scheduled dependency vulnerability scanning (`pip-audit` over every locked environment), a Trivy image scan gate between build and push in the release workflow, digest-pinned base images, and Dependabot updates for `uv.lock`, GitHub Actions, and Docker base images.

### Changed

- The Python floor is `>=3.12` for every package.
- LiteLLM was bumped from 1.80.5 to 1.98.0, clearing the identified fixable advisories and restoring compatibility with the exact `pydantic` pin.

### Fixed

- Docker builds copy package metadata files required by wheel builds, and `.gitattributes` forces LF for shell scripts so images build from Windows checkouts.

### Security

- Hardened image acceptance: arbitrary-UID/read-only-root execution, 2 GiB size gate, health/readiness checks, and genuine stop/restart/resume verification for both production engines.
- Policy, cancellation, timeout, output-bound, cleanup, and restart/ambiguous-failure coverage for sandbox execution across engines and controllers.
