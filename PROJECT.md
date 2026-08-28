# Project Context

## Source of Truth and Current Phase

`Project Definition.md` is authoritative; `AGENTS.md` contains mandatory contributor rules and `TODO.md` is the active backlog. Core implementation, local acceptance, remote CI/release verification, the applicable CTK gate, configured PostgreSQL persistence acceptance, B-001, B-002, B-003 (bounded eventing/CloudEvents/scheduling/sub-workflow/HITL), B-004 (secure external catalog), B-005 (internal sandbox), and the current B-006 external-sandbox baseline are complete. PRs #14 (Kubernetes/OpenShift sandbox boundary plus hosted Docker acceptance), #15 (bounded lifecycle SSE), and #16 (Microsoft Agent Framework native adapter) are merged to `main`.

The Kubernetes/OpenShift deployment boundary still needs real-cluster acceptance. Lifecycle SSE is intentionally bounded to lifecycle events; general portable output streaming and inbound A2A remain deferred. The Agent Framework adapter is available as an optional native package but is not yet a production image/release target.

## Architecture

The runtime is `load -> official schema validation -> Portable Profile gate -> normalize -> immutable internal plan -> engine execution`. Core is framework-neutral. `engines/adk` and `engines/langgraph` are separate packages with exact locks, native agent/tool adapters, and engine-owned state; `engines/agent-framework` is an optional third adapter with its own exact lock (not a release target). Every request executes a workflow, with a generated default workflow when none is supplied.

Standard runtime images bundle the common LiteLLM model adapter/runtime for configured external model providers and FastEmbed 0.8.0/ONNX with the 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model identity for local knowledge embeddings. Deterministic `FakeModel`/hash embeddings remain available for tests so CI never requires paid model access.

`sandbox-controller/` and `kubernetes-sandbox-controller/` are restricted external-sandbox controller packages with digest-pinned images published by the release pipeline. Supply-chain gates: a scheduled/lockfile-triggered Security workflow audits every locked environment with pip-audit, the release pipeline scans every published image with Trivy before push, base images are pinned by digest, and Dependabot updates all `uv.lock` files, GitHub Actions versions, and Docker base images.

## Repository Structure and Conventions

- `core/`: common configuration, schema, workflow semantics, catalogs, services, API, persistence metadata, approval/schedule state, and errors.
- `engines/adk/`, `engines/langgraph/`: independent adapters, native persistence, locks, and package metadata.
- `resources/`, `runtime-catalog/`: official resources and built-in catalog.
- `docker/`: independent multi-stage runtime images; `.github/workflows/ci.yml`: Ubuntu quality/container gates; `.github/workflows/release.yml`: verified GHCR release publication.
- `tests/core`, `tests/contract`, `tests/adk`, `tests/langgraph`, `tests/ctk`, `tests/e2e`: layered deterministic coverage.
- `core/src/open_workflow_agent/protocols.py`: bounded common HTTP/MCP/A2A/OpenAPI clients used by workflow calls and configured agent tools.
- `core/src/open_workflow_agent/approvals.py`: bounded durable approval state and replay layered on standard event/listen semantics.

Use strict typed Python, four-space indentation, exact dependency locks, shared contract fixtures, and `FakeModel`; tests must not require paid APIs. Do not install packages at container startup.

## Verified Status

Root format/lint/mypy/tests/contracts, ADK/LangGraph native suites, selected CTK, Docker image/health/knowledge/restart-resume gates, and PostgreSQL persistence acceptance remain green. SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented behind locked `postgres` extras with isolated namespaces. Real model providers are selected through configuration and provider-specific secrets; no source checkout or image rebuild is required for the standard LiteLLM path.

### Local verification after the 2026-08-27 backlog sweep

- Root quality gates: `270 passed, 11 skipped` with pytest-cov at `82.75%` (CI gate `--cov-fail-under=80`), ruff format/lint clean, mypy strict clean over `core/src`.
- ADK contracts/CTK `104 passed` (new shared `fork`/`wait` CTK scenarios and the `try` contract fixture); LangGraph contracts/CTK `104 passed`; Agent Framework native `5 passed`.
- All seven packages build; every `uv.lock` passes `uv lock --check`.
- Python floor aligned to `>=3.12` for all packages (numpy fork removed); LiteLLM bumped 1.80.5 → 1.98.0 after the new pip-audit gate flagged fixable advisories (GHSA-69x8-hrgq-fjj8, PYSEC-2026-3476); all six locked dependency environments are advisory-clean.
- The knowledge manifest now records an `indexed_at` timestamp plus real parser identities (`pypdf@<version>`, `pyyaml@<version>`, `stdlib-json`, `text`) and the `whitespace-window:<size>+<overlap>` chunking identity, with a migration for existing SQLite/PostgreSQL databases.
- Docker builds re-validated locally for all four images: package metadata files (per-package README/LICENSE) are copied into the build stages, `*.sh` is forced to LF via `.gitattributes` so Windows checkouts build, and the rebuilt runtime images pass the litellm import check plus a live readiness/capabilities/invocation check under arbitrary-UID/read-only-root.

### Release v0.1.0 (2026-08-28)

The first formal release was cut from the verified head `c47cb86` (`v0.1.0`). The Release run `33136714445` passed the companion-acceptance gate, scanned all four images with Trivy before push, published to both registries, and created the GitHub Release with generated notes and pull commands.

Acceptance runs for the release head (2026-08-28):

| Workflow | Run | Result |
| --- | --- | --- |
| CI (quality, contracts, CTK, kubeconform manifests, Docker acceptance) | `33136592588` | green |
| Security (pip-audit over all six locked environments) | `33136597832` | green |
| External Sandbox CI (Docker acceptance, controller image checks) | `33136592592` | green |
| PostgreSQL CI | `33136592632` | green |
| Release (four images, Trivy gate, attestations, GitHub Release) | `33136714445` | success |

Published `0.1.0` images (identical manifests under `latest`, `0.1`, and `sha-c47cb86`):

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk                        sha256:4d89ffaa88207488fec4b128e1e728282cca701f0e31330c7314c1606235cf36
ghcr.io/bassemzohdy/open-workflow-agent-langgraph                  sha256:add38f52c062a01ab81c61962ab609a728e62a367818cc77bff19a6a720d2a89
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller         sha256:d00394b821136a50fd73f2ac16217632f0d09844d837141f316d93fa2e00cb05
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller sha256:97c31c39a5a769b5e248b5e7b0b94455102be6bd757efbbec1f5329c5da2e520
docker.io/bzohdy/open-workflow-agent-adk                           sha256:4d89ffaa88207488fec4b128e1e728282cca701f0e31330c7314c1606235cf36
docker.io/bzohdy/open-workflow-agent-langgraph                     sha256:add38f52c062a01ab81c61962ab609a728e62a367818cc77bff19a6a720d2a89
```

At v0.1.0 the sandbox-controller images are published to GHCR only; Docker Hub mirroring for controllers was added to the workflow for future releases. OCI SBOM/provenance metadata covers every published image and GHCR build-provenance attestations are published for the canonical GHCR manifests. Dependabot is active (weekly uv/Actions/base-image update PRs; the first update PRs arrived on release day).

### Prior acceptance record (`main` at `80bfa2b`, pre-sweep)

All acceptance gates for the current integration head are verified green (2026-08-27):

| Workflow | Run | Result |
| --- | --- | --- |
| CI (quality, contracts, CTK, image acceptance, knowledge, catalog, internal sandbox, lifecycle/restart) | `33105068629` | green |
| PostgreSQL CI (common stores plus ADK/LangGraph persistence/restart) | `33105068561` | green |
| External Sandbox CI (ADK/LangGraph Docker acceptance, Kubernetes controller image checks) | `33105068565` | green |
| Release (ADK/LangGraph publication) | `33105399880` | success |

Published images for this head (`latest` and `sha-80bfa2b7887a` tags on both registries, same digests):

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk       sha256:298e0dca885813b2bc1eb5449be2968ca466f5853215d1024f80442d1c067380
ghcr.io/bassemzohdy/open-workflow-agent-langgraph sha256:f65c01524cfff0acbc8aa3a3396fd1d93738e0f3382cfe4df4d2f4c238ee32c0
docker.io/bzohdy/open-workflow-agent-adk          sha256:298e0dca885813b2bc1eb5449be2968ca466f5853215d1024f80442d1c067380
docker.io/bzohdy/open-workflow-agent-langgraph    sha256:f65c01524cfff0acbc8aa3a3396fd1d93738e0f3382cfe4df4d2f4c238ee32c0
```

The release build carries OCI SBOM/provenance metadata and GHCR build-provenance attestations for both images. Local verification at this head: root `247 passed, 11 skipped`, ADK contracts/CTK `98 passed`, LangGraph contracts/CTK `98 passed`, Agent Framework native tests `5 passed`, plus formatting, lint, mypy, lock checks, package builds, and shell syntax checks.

### Prior verification history

CI run `32915495802` verified the standard images after adding locked LiteLLM 1.80.5 to both independent engine dependency graphs, and run `32930787715` passed every root, engine, CTK, Docker, and PostgreSQL job for commit `75be75603620a4155fd49e8e4f89d721bb437dec`. The external-catalog hardening slice (one-shot DNS resolution with public-address validation and a pinned HTTP transport that connects only to approved addresses while preserving hostname-based TLS verification) passed GitHub Actions run `32945536005` for commit `9c9dfa0edbb201a68947dc95fbd8860791cb6a49`.

Verified standard image sizes after the 2026-08-27 dependency refresh (LiteLLM 1.98.0, Python 3.12-only numpy, digest-pinned `python:3.12-slim` base), with local FastEmbed/ONNX knowledge embeddings, PostgreSQL support, and native engine dependencies:

```text
ADK        266,258,126 bytes (~266 MB decimal)
LangGraph  247,867,843 bytes (~248 MB decimal)
```

Both builds were validated locally: the LiteLLM import check passes, a runtime container starts under an arbitrary UID (`12345:0`) with a read-only root filesystem, readiness/capabilities/invocation all succeed, and the new HEALTHCHECK/STOPSIGNAL metadata is present. The images remain far below the 2 GiB gate and avoid the multi-gigabyte Torch/CUDA dependency path. The Docker build performs an explicit LiteLLM import check, and package metadata files (per-package README/LICENSE) are copied into the build stages so wheel builds succeed inside the image.

## Completed B-003 Behavior

- Generic `emit`/`listen(one)` event delivery is process-local and non-durable.
- Lifecycle events are available as bounded CloudEvents 1.0 JSON snapshots.
- `schedule.after` and `schedule.every` persist scheduler state with restart reclaim and at-least-once semantics.
- Local sub-workflows use the standard `run` task against deployment-configured `workflow.catalog` definitions.
- Durable HITL approvals use standard event composition rather than a proprietary task: approval request/decision state is persisted separately, operator decisions are bearer-authorized and idempotent, inbox reads are protected, and terminal decisions replay through the normal `listen` path after restart. ADK and LangGraph share the same observable contract. The bearer/operator-header guard is deliberately a bounded deployment authorization boundary, not a replacement for an enterprise identity provider.

## Release Images

Verified stable tags are published by `.github/workflows/release.yml` only after CI succeeds for the tagged commit, the companion External Sandbox/PostgreSQL acceptance gate passes, and the Trivy image scan is clean:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<version>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<version>
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller:<version>
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller:<version>
```

The release pipeline adds exact-version, minor-series, `latest`, and immutable source-SHA tags plus SBOM/provenance metadata and GitHub build provenance attestations. Publication runs inside the protected `release` GitHub environment.

## Current Next Step

The first formal release **v0.1.0** is published (2026-08-28) from the verified head `c47cb86`. Remaining work is acceptance- and decision-gated:

1. B-006.3 — Kubernetes/OpenShift real-cluster acceptance (timeout/cancellation/restart cleanup, retained-log secret safety, namespace/RBAC enforcement, OpenShift SCC/security-context/arbitrary-UID behavior) before advertising `run.container` for those backends through `/v1/capabilities`. No cluster is available in the current development environment; Docker external sandbox production acceptance is recorded green.
2. B-007 — decide the next bounded slice: inbound A2A, general portable streaming, or continued deferral.
3. B-008 — decide whether the Microsoft Agent Framework adapter becomes a production engine. Progress: the adapter now passes the full shared contract surface and CTK subset natively (139 tests) and CI enforces those suites; an independent runtime image, persistence/resume coverage, and hardened-image acceptance remain before any third-engine advertisement.

Full MCP, A2A, OpenAPI, external-catalog, streaming, or Open Workflow ecosystem conformance remains unclaimed beyond the tested Portable Profile/capabilities.

## Key Commands

```text
uv sync --locked
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q --cov=core/src/open_workflow_agent --cov-fail-under=80
uv build
uv build --directory core
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
uv run --directory engines/agent-framework --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/agent_framework -q
uv run --locked pytest tests/core/test_protocols.py tests/contract/test_contract.py tests/contract/test_tools.py -q
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```
