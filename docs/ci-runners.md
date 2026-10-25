# CI Runners and Validation

The workflow files define the required checks. During the current backlog
batch, changes stay local until ready for one comprehensive validation pass.
Task checkboxes stay open until their required evidence passes. This cleanup
is verified at `3f568e5`: all six configured workflows pass, including actual
PostgreSQL, four Windows selections and both main/database image pairs.

## Triggers and duplicate work

Feature branches are validated through their pull requests. The main CI and
dependency audit no longer also run on every feature-branch push. Main-branch
push validation remains available for the publication workflow's exact source.
Standalone branch validation can be requested through the main CI's manual
trigger. Weekly and manual dependency audits remain enabled.

| Workflow | Coverage | Trigger |
| --- | --- | --- |
| [CI](../.github/workflows/ci.yml) | Root quality/coverage, Windows admission and native recovery, both principal native/shared/CTK suites, benchmarks, builds, manifests, mutation and main runtime images | Pull requests, non-documentation main pushes, manual |
| [PostgreSQL CI](../.github/workflows/postgres-ci.yml) | Actual common/native database recovery, both database-backed runtime images, and the required matched S4-06 engine/backend performance report | Relevant pull requests, non-documentation main pushes, manual |
| [External Sandbox CI](../.github/workflows/external-sandbox-ci.yml) | Restricted controller contracts, image defaults and actual Docker acceptance for both principal engines | Relevant pull requests, non-documentation main pushes, manual |
| [Agent Framework CI](../.github/workflows/agent-framework-ci.yml) | Optional native adapter, shared contracts and CTK in its independent environment | Relevant pull requests/main pushes, manual |
| [Security](../.github/workflows/security.yml) | All seven locked dependency environments and their optional dependency profiles | Relevant pull requests, every main push, weekly, manual |
| [Docs](../.github/workflows/docs.yml) | Relative Markdown links | Pull requests and every main push |

The optional Agent Framework workflow also watches common core and shared
fixtures. A core-only change must not silently bypass that adapter's checks.

Standalone core has its own required lock check and complete optional-dependency
audit. The child batch found its older lock missing already-declared timer
dependencies and security updates. The targeted correction preserves its
declared package versions, adds croniter, updates tzdata/pypdf and patches the
two vulnerable transitive dependencies. Seven environments are now audited.

## Execution and coverage

All current jobs use GitHub-hosted Ubuntu or Windows runners. External Docker
acceptance uses the hosted Ubuntu runner's Docker daemon; a self-hosted runner
is not a current prerequisite. Kubernetes/OpenShift real-cluster acceptance is
separately owned and recorded by DEPLOY-1 in [PROJECT.md](../PROJECT.md).

The root full-suite command explicitly selects `tests/`, including shared
contracts, and retains the 90% core coverage threshold. A second identical
root contract run is removed. Each principal SDK still runs the shared suite
in its own locked environment, plus its native tests, portable CTK subset and
benchmark probes. Root fallback results do not qualify a native SDK.

Windows admission, native routing, effect/ownership recovery and timer/event
recovery have independent bounded jobs. All existing selections are retained;
each keeps its twenty-minute limit. This avoids one long sequential selection
consuming the available allowance for unrelated recovery cases.

PostgreSQL common stores, LangGraph, ADK and optional Agent Framework each run
with a fresh database in independent twenty-minute jobs. Principal recovery
commands have fourteen-minute bounds after the expanded ADK selection reached
89% without an assertion failure at its former nine-minute limit. Both
database-backed images require all four integration jobs to succeed.

The matched S4-06 report runs ADK and LangGraph sequentially on Ubuntu with
both SQLite and an isolated PostgreSQL service. Its aggregate artifact requires
all four profile cells to share the exact source commit and measurement
parameters. The report is measurement evidence, not a portable performance
claim; see [matched performance measurements](benchmarks.md) for its metrics
and limits.

Main native tests and container acceptance read the same source independently;
they consume no artifact from the root job or each other. Those unnecessary
job dependencies are removed. Both matrix members can finish when one fails,
giving complete evidence in the final run. Workflow failure still prevents
publication. Stale pull-request checks are cancelled by concurrency groups;
main-branch acceptance is not cancelled by that policy.

## Publication and provenance

The [Release workflow](../.github/workflows/release.yml) remains separate from
feature-branch validation. Its existing safeguards are preserved:

1. Successful main **push** CI at the exact publication source.
2. Successful External Sandbox, PostgreSQL, Security, and Docs runs at that
   same commit. Agent Framework remains optional and outside production
   publication. Ancestor success never substitutes for exact-source acceptance.
3. Main-head checks before and after the companion wait, plus serialized release
   publication, so a superseded commit cannot overwrite current release tags.
4. Runtime and controller vulnerability preflight for both image architectures
   before publication, with SBOM/provenance and registry metadata retained.
5. The named `release` environment and completion of both runtime/controller
   publication before creating a formal GitHub release.

Repository rulesets, required reviews and environment protection are managed
in repository settings. These local workflow edits do not change them or
authorize a merge, deployment or release. Optional Agent Framework remains
outside the production image/release targets.

## Failures and retained evidence

Capture the exact source, event, run/job IDs, terminal conclusions and artifact
hashes for final qualification. A cancelled job is not a pass; a collection
guard or unavailable-backend skip is not actual database/native execution.
Historical duplicate push/PR runs remain valid historical records and are not
deleted or relabelled by the trigger cleanup.

If a job is queued without a runner, distinguish hosted capacity from workflow
configuration using its job allocation and runner labels. Running validation
does not by itself make a backlog task blocked. Investigate actionable failures;
defer a task only under the full blocked-task rule in [AGENTS.md](../AGENTS.md).
