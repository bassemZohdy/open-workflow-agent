# Release integrity review — 2026-09-24

## Executive summary

**Assessed baseline:** `95aeb02f991d35bc265467e0e4a03f05e732d9d0`
(`main`, clean working tree). **Scope:** release/supply-chain behavior,
architecture boundaries, and the current evidence needed to select the next
in-scope work. This is a focused architecture review, not a new conformance
assessment or an approval record.

The current open-source core has no unfinished product milestone. ADK and
LangGraph have separate packages and locks; core has a common workflow and
sandbox boundary; the bounded A2A and lifecycle profiles are explicitly
advertised. The exact baseline passed CI, PostgreSQL CI, External Sandbox CI,
and Release. Optional Agent Framework production support and external event
integration still require a concrete user/deployment requirement.

The next work is release integrity maintenance. A previous main build
(`64dc212`) published the Docker controller image while scans blocked the
other three images. The subsequent `95aeb02` run fixed those scan findings,
but the workflow still allows a partially updated image family when one
pre-publication scan fails.

## Architecture and evidence boundary

| Area | Observed evidence | Assessment |
| --- | --- | --- |
| Public workflow contract | `Project Definition.md` §§3–9; common plan and engine SPI | No new DSL or engine work justified |
| Execution isolation | `core/src/open_workflow_agent/sandbox/manager.py`; no independent process path in engine sources | Common sandbox boundary retained |
| Portability and verification | Shared contract/CTK suites; green [main CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/35933526556) | Strong deterministic evidence for the advertised profile |
| Production acceptance | Green [PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/35933526627) and [External Sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/35933526511) runs at `95aeb02` | Current code paths accepted |
| Publication | Green [Release](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/35933779422) at `95aeb02`; failed [Release](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/35932627281) at `64dc212` | Publication gates need tightening |

The review did not prove production traffic behavior, every upstream protocol
operation, or full supply-chain immutability. Formal `v0.2.0` remains the
current release; the recent green run is a rolling `main` publication.

## Findings

| ID | Severity / confidence | Evidence and impact | Owner / verification |
| --- | --- | --- | --- |
| R1 | High / high | `.github/workflows/release.yml` publishes each image immediately after its own amd64 scan. Run `35932627281` published one controller while three scans failed, leaving an inconsistent rolling image set. | Release maintainer: make all scans prerequisites of every push; inject a failed scan in workflow tests and verify no publish job is eligible. |
| R2 | High / high | The release `prepare` job accepts the latest completed External Sandbox/PostgreSQL run when its SHA is merely an ancestor of the release head. Run `35933779422` began while same-head External Sandbox CI was still in progress. A changed input can therefore publish before its acceptance result. | Release maintainer: wait for exact-head runs with a deadline; test delayed, failed, and missing runs. |
| R3 | High / high | Both publish jobs scan only `linux/amd64` but push `linux/amd64,linux/arm64`; `docs/release-readiness.md` calls for a scan on every target platform. An arm64-specific finding can reach the registry. | Release maintainer: scan all four images on both platforms before pushing; test the complete matrix. |
| R4 | Medium / high | The `github-release` job needs `prepare` and `publish`, but omits `publish-controllers`. A formal GitHub Release could be created without both controller images. | Release maintainer: include controller publication in the dependency graph and test it. |
| R5 | Medium / high | `PROJECT.md` records `v0.2.0` digests but not the green rolling maintenance publication at `95aeb02`. `README.md` and `docs/deployment.md` call `sha-*` tags immutable even though a rerun can republish a tag. Deployers need exact digests for stable pinning. | Maintainer: record all four resolved digests and clarify tag/digest wording; verify registry references. |
| R6 | High / high | Release concurrency is grouped by commit, so two eligible `main` commits can publish at once. A slower older run can update `latest` after a newer run. Exact-head acceptance waits make this race more likely. | Release maintainer: serialize the release workflow across commits, skip a superseded head before and after companion acceptance, and test the queue and guards. |

## Priority and limits

The ordered remediation is `RELEASE-4` through `RELEASE-8` in `TODO.md`.
It is confined to publication evidence and gates. No Open Workflow schema,
execution plan, engine checkpoint contract, sandbox policy, or optional
Agent Framework/event surface should change.

Preflight scans prevent a *scan failure* from causing partial publication.
Registry/network failure after preflight can still leave some tags updated;
the release workflow cannot provide a transaction across GHCR and Docker Hub.
Deployment should pin digests, and release documentation must state this limit.
The head can advance after the final freshness check and before a push;
serialization ensures that a subsequently eligible run follows, but `latest`
can briefly refer to the older commit. A failed newer run can leave that tag
behind. Consumers requiring exact provenance should pin a verified digest.
The project maintainer owns any decision to require a future staging/promotion
system. That larger change is outside this milestone.
