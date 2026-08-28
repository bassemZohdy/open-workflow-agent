# Changelog

All notable changes to the Open Workflow Agent are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- External sandbox backends: deployment-selected Docker backend with a restricted Unix-socket controller, approved-image policy, and fail-closed `run.container`; Kubernetes/OpenShift controller boundary with deployment-owned namespace/ServiceAccount/network-policy controls (real-cluster acceptance pending, see `TODO.md` B-006.3).
- Bounded protocol clients (HTTP/MCP/A2A/OpenAPI) with timeouts, TLS verification, response-size limits, redirect policy, and authentication abstraction.
- Microsoft Agent Framework native adapter as an optional third engine package (not yet a production image/release target).
- Restricted Docker and Kubernetes sandbox-controller packages with digest-pinned images published through the release pipeline (Trivy-scanned, provenance-attested) and a `compose.sandbox.yaml` reference wiring.
- Knowledge manifest entries record an index timestamp, the real parser identity (e.g. `pypdf@<version>`, `pyyaml@<version>`), and the chunking algorithm identity, with an automatic migration for existing databases.
- Reference Kubernetes/OpenShift runtime manifests under `deploy/`, validated in CI with kubeconform; HEALTHCHECK/STOPSIGNAL on runtime images and Compose services.
- Continuous image publication to GHCR and Docker Hub from green `main`, with exact-version/minor-series/`latest`/source-SHA tags on tagged releases plus OCI SBOM/provenance metadata and GHCR build-provenance attestations.
- Scheduled dependency vulnerability scanning (`pip-audit` over every locked environment), a Trivy image scan gate between build and push in the release workflow, digest-pinned base images, and Dependabot updates for `uv.lock`, GitHub Actions, and Docker base images.

### Changed

- The Python floor is now `>=3.12` for every package (previously declared `>=3.11` while CI, mypy, and all images targeted 3.12 only); the `numpy` 3.11 compatibility fork was removed.
- LiteLLM was bumped from 1.80.5 to 1.98.0, clearing known fixable advisories (GHSA-69x8-hrgq-fjj8, PYSEC-2026-3476) and restoring compatibility with the exact `pydantic` pin.

### Fixed

- Docker builds now copy the package metadata files (per-package `README.md`, root `LICENSE`) required by the wheel builds, and `.gitattributes` forces LF for shell scripts so images build from Windows checkouts.

### Security

- Hardened image acceptance: arbitrary-UID/read-only-root execution, 2 GiB size gate, health/readiness checks, and genuine stop/restart/resume verification for both engines.
- Policy, cancellation, timeout, output-bound, cleanup, and restart/ambiguous-failure coverage for sandbox execution across engines and controllers.
- LiteLLM 1.80.5 had known fixable advisories (GHSA-69x8-hrgq-fjj8, PYSEC-2026-3476) addressed by the 1.98.0 bump.
