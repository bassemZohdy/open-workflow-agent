# S4-07 release gates and publication evidence

## Exact-source qualification f66cd23

The finite task profile is verified at `f66cd23b399a707fda0683e180c9f34c7b6d6408`. All six source-matched hosted workflows pass, including CI attempt 2 after the retained Windows file-handoff failure. Source/version matched validation and image gates, immutable matched release-image manifest generation and required assembly, explicit operator rollback policy and separately opt-in automation. No publication, deployment, registry/index ownership claim, automatic rollback/checkpoint migration or optional-engine production target is performed or qualified.

See the [complete qualification and prior failures](validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-f66cd23). Full research/proposed/schema criteria remain separately unverified. Later native Send and parity changes retain their own gates.

## Earlier authored history

Status: local workflow guards and release metadata checks passed in the
corrected consolidated batch; S4-07 remains open pending exact-source hosted
qualification. Automatic validation requires `OWA_AUTOMATIC_CI=true`; publication
also requires `OWA_AUTOMATIC_RELEASES=true`. Manual validation remains available.
When enabled, publication follows successful main-push CI and
requires successful PostgreSQL, External Sandbox, Security, and Docs runs for
the same commit. Missing runs remain pending until timeout; failed runs stop
publication. Agent Framework remains an optional evaluation adapter and is not
a production image or release gate.

With automatic CI enabled, Security and Docs run on every main push so an exact-head result is available
to the gate. Their pull-request triggers remain scoped to their normal
validation needs. A documentation-only main commit does not start publication
by itself because Release is triggered by successful CI.

Before publication, the Release workflow checks that root, core, ADK, LangGraph,
sandbox-controller, and Kubernetes sandbox-controller package versions match.
Tests read the package metadata and assert that every published package is
covered by the workflow check. All eight runtime/controller image and
architecture pairs must pass vulnerability preflight before any push; the
workflow retains SBOM, provenance, and attestation steps. Stale-main checks
before and after acceptance, plus serialized release concurrency, prevent an
older commit from publishing current tags. Formal GitHub release creation
waits for both runtime and controller publication.

A release-workflow regression now requires both runtime and controller image
publication paths to carry the verified source URL, project version, and exact
workflow-run commit as OCI labels, while preserving SBOM and provenance
generation. The guard passed in the consolidated local suite.

The `370d9cd` hosted root-quality run also exposed a 5-second readiness timeout
in a sandbox control-fence fixture under runner load. The test now provides a
larger startup/deadline margin; both stale and current-generation cases pass
locally. Exact-head hosted requalification remains pending.

Final acceptance must validate the Release workflow, exact-head gate selectors,
all required hosted workflows, package/image metadata, and rollback records on
one unchanged source commit. Exact-head hosted runs for the corrected source
batch—including Security after its audit-resolver adjustment—remain pending.
The focused local Security audit uses pip-audit's `--disable-pip` mode with the
fully pinned export and confirms FastEmbed and Pillow remain in the report; it
does not substitute for that hosted exact-head gate.
This record does not claim a release, production
deployment, or successful qualification for the current source. It does not
change repository rulesets, environment protection, support policy, or public
isolation guarantees.

## Immutable release-record candidate — 2026-10-25

The publication workflow now retains one validated record per actual pushed
runtime/controller digest. A required assembly job accepts exactly four unique
images from the same commit, version and publication run, with both registry
references and the two declared platform targets. Mutable tags, mismatched
digests, foreign fields, missing/duplicate images and mixed source/run identity
reject before a combined record is written. The GitHub Release job requires
that assembly and attaches `owa-release-manifest.json`; non-tagged published
main builds retain the same manifest as a workflow artifact.

The records use build-push's actual manifest digest, independently of rolling
tags. They preserve the existing SBOM/provenance and exact-source gates; they do
not independently verify registry contents or execute a rollback. Operators
retain a previously qualified matched manifest, compatible configuration and
committed recovery evidence. No credentials, automatic database restoration or
checkpoint migration are included. Earlier releases need their original verified
digest records; this candidate does not invent historical manifests.

The initial manifest/workflow/gate/automation selection passes 33 local tests,
including real CLI file output, unchanged output on malformed input and synthetic
metadata rejection. These are validator tests, not actual publication. Strict
typing and formatting pass. The complete local CI-guard suite passes 213 tests.
Coherent hosted qualification remains pending; no release or deployment was
performed.
