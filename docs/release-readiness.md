# Release-readiness checklist

This is a reusable checklist for a future release, not a live project-status
board. The completed `v0.2.0` release record is in `PROJECT.md`; its checklist
items remain unchecked here so the document can be copied and evaluated against
the exact commit for the next release.

Complete this checklist from the exact commit intended for publication. A release is
not ready when any required gate is missing, stale, or green for a different commit.

## Metadata and source

- [ ] Record the commit SHA and intended release tag/version.
- [ ] Confirm the root, core, production-engine package versions, and runtime version agree.
- [ ] Confirm `CHANGELOG.md`, `PROJECT.md`, `TODO.md`, and release-facing documentation are current.
- [ ] Confirm no credentials, local state, generated caches, or unrelated repository changes are present.

## Reproducible quality gates

- [ ] `uv lock --check` passes for root and every independently locked package.
- [ ] Root format, lint, type, unit/contract, coverage, mutation, manifest, and wheel gates pass.
- [ ] ADK and LangGraph native, contract, and Portable Profile CTK suites pass.
- [ ] Optional Agent Framework evaluation gates pass, or its known non-production status is unchanged.
- [ ] Documentation link validation passes when documentation changed.

## Runtime acceptance

- [ ] Docker runtime acceptance passes for every production engine.
- [ ] Stop/restart/resume acceptance passes without exposing engine-native state.
- [ ] External Docker/Kubernetes sandbox acceptance passes for the intended platform set.
- [ ] PostgreSQL common-store and engine persistence acceptance passes when those images are published.
- [ ] OpenShift acceptance is green before advertising OpenShift container execution.

## Supply chain and publication

- [ ] Dependency audit has no fixable high/critical advisories; unresolved findings have a dated rationale.
- [ ] Image scan passes for every published image and target platform.
- [ ] OCI SBOM and provenance/attestation generation succeeds.
- [ ] Published image tags and digests are recorded in `PROJECT.md`.
- [ ] Companion acceptance runs cover the exact release commit according to the release workflow.
- [ ] GitHub Release notes and rollback image references are prepared.

## Exceptions and sign-off

Record every exception with an owner, rationale, mitigation, and expiry date. Deferred
features such as A2A push notifications, delegated identity, full protocol conformance,
and multi-tenancy must not be described as release capabilities.
