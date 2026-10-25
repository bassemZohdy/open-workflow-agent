# Ready-task qualification — 2026-10-25

The user requested completion of the open backlog and explicitly kept S4-09,
S4-10 and S4-11 conditional. Those tracks remain inactive. The 14 ready task
entries retain their original scope and gates; this record does not close them.

## Frozen baseline

Commit `6f9ef5d0d8ea24a75cddca1b2b57060ee546f4bb` captures the preceding development
cleanup and reference/admission fixes. Full independent ADK/LangGraph contract,
adapter and selected CTK suites run against its immutable Linux source, with
actual PostgreSQL and complete Git history for archived-source recovery tests.
The Windows root suite uses its own source copy and locked environment. Results
remain pending until each process exits and its report is verified. The frozen
Windows root run subsequently exits zero with 5,072 passes, 3,226 expected
environment/platform skips and 90.56% coverage. Its input manifest remains
unchanged. This qualifies that historical root source only; it predates the
dependency, credential-fixture and JavaScript corrections below.

Two initial Windows runs were deliberately stopped: the first used the live
working directory; the second used the frozen source with installed editable
metadata pointing to the live checkout. The latter correctly failed recovery
provenance. Installing the frozen checkout's own locked environment resolves
that mismatch; the three failed environment-smoke cases then pass. These partial
runs and failures are retained, and are not passing qualification evidence.

The initial Linux runs were interrupted after identifying test/environment
corrections: ADK retained 388 passes, 16 skips and three failures; LangGraph
retained 669 passes, 26 skips and six failures. No aggregate success is claimed.
Some cross-adapter fallback cases correctly rejected the production-wheel test
environment, which lacked CI's explicit editable-root provenance. The next
test-only image setup installs that root at build time and checks both adapter
profiles before running the suite. Production image provenance and runtime
recovery guards remain unchanged.

The initial native ADK run found a stale test expectation that a newly created
handle had status `created`; the public contract initializes it as `running`.
The corrected test compares the full common SQLite dump before/after unsupported
extension admission, along with no native file or provider call. It passes
against the actual SDK. No invocation-state contract or admission behavior is
changed.

## Dependency corrections

The initial seven-profile Windows advisory scan fails all five runtime profiles
and passes both controllers. It identifies FileLock/PyPDF advisories and older
OpenTelemetry API versions in ADK/optional Framework, plus the PostgreSQL
checkpoint namespace advisory in LangGraph. The advisory's upstream record also
identifies the SQLite checkpoint version as affected.

The correction uses PyPDF `6.21.2`, a resolver constraint of FileLock
`>=4.2.1,<5` (locks resolve `4.2.4`), LangGraph SQLite checkpoint `3.1.2`,
PostgreSQL checkpoint `3.1.3` and their required common checkpoint `4.3.0`.
Optional Framework resolves OpenTelemetry API `1.45.1` without overriding its
dependency bounds.

ADK `2.7.1` caps both OpenTelemetry API and SDK at `1.42.1`; even the reviewed
stable `2.13.0` metadata caps them below the fixed API. The ADK environment
therefore pairs exact API/SDK `1.45.1` through an explicit compatibility override,
retaining its existing ADK version. Native/backend compatibility must be proven
before this override qualifies a release. The independent lock files remain
separate; no package installation is added to runtime startup or sandbox work.

All seven corrected Windows dependency exports pass the existing strict audit
policy, without ignored advisories or removed optional dependency families.
The corrected native adapter selections pass 42 ADK tests and 69 LangGraph tests;
the latter has seven unconfigured PostgreSQL skips which are not acceptance for
that backend. Current-source PostgreSQL, full-suite, image and hosted gates still
remain required.

The real PDF knowledge fixture now creates a valid text page and checks actual
parser ingestion and retrieval with a deterministic local embedding. It passes
with the patched parser; the security/build/knowledge selection passes 40 tests.
Strict mypy passes four CI and 136 runtime/adapter modules. Ruff, changed-file
formatting and documentation links pass.

## Native credential witness correction

The first native role-canary fixture was denied before credential resolution:
the shared caller policy deliberately has no `credential.resolve` grant. Its
provider also returns a JSON string, while the selected recipe requires an
object with an `answer` field. The corrected witness grants only Alice access
to that one named model secret and uses the standard `output.as` JSON transform,
preserving the recipe's existing output schema. Lifecycle reads authenticate
through the actual configured API profile and an exact `events.read` grant.

The final witness also runs without the credential grant, where neither secret
resolution nor provider dispatch may occur. Actual SDK checks pass all eight
ADK/LangGraph × SQLite/PostgreSQL × allow/deny cases with the patched libraries.
They inspect retained common rows, the archive, real native checkpoint rows,
lifecycle events, logs, public results and the authenticated lifecycle response
for the literal canary. This is direct-echo protection, not encoded-secret or
general information-flow proof. Earlier failed reports remain retained.

The complete in-process eight-family pattern suites pass 60 tests per principal
SDK against both databases with the updated fixture and patched libraries.
The source `88ca0f5` hosted checkpoint passed Security, Docs, External Sandbox
and optional Framework. PostgreSQL passed nine jobs, including all matched
measurements and aggregation, but each principal operation job failed only the
uncorrected positive canary fixture (240 passed, one failed per SDK). CI is
complete and failed only that same uncorrected SQLite canary in each principal
native suite and the Windows timer lane. The ADK native suite has 1,449 passes,
LangGraph 1,511, and the Windows timer lane 742; each has one failure. Root
quality passed 5,069 tests with 3,230 environment/profile skips and 90.14%
coverage. The other 18 CI jobs passed. These failures are preserved and do not
qualify the candidate; the following source must re-run the exact-source gates.

## Matched report identity correction

The retained `88ca0f5` four-cell report records runner name `local` because the
harness read the nonstandard `GITHUB_RUNNER` variable. It now prefers the
standard `RUNNER_NAME`, retains the explicit legacy fallback and identifies a
local run only when neither is present. Five parameterized cases cover those
paths; the original raw report remains unchanged. The benchmark aggregation and
runtime helpers now pass strict mypy, which is included in the Linux root gate.
The benchmark/role selection passes 55 tests with 25 unconfigured PostgreSQL
skips; actual PostgreSQL credential checks are recorded separately above.
Fresh source-matched measurements are still required after this correction.

Source `67279ed` fails the root job before tests: the new benchmark mypy command
imports the optional Psycopg module, but that root environment selects knowledge
only. Job `121106101973` reports `import-not-found` for `psycopg`; earlier core/CI
typing passes. The corrected benchmark type gate explicitly selects the existing
`postgres` extra, retaining PostgreSQL as an optional runtime dependency. This
is a CI environment correction, not ignored typing or relaxed coverage. The
remaining `67279ed` jobs retain their own outcomes; this source is not an
aggregate acceptance checkpoint.

Its Windows operation job `121106101729` also fails one cancellation/takeover
case (1,059 passed, 475 skipped). The first cancel observation is `unknown`;
the log does not retain authenticated state/heartbeat evidence to identify why.
The fixture now preserves the submitted control bytes and requires a later
authenticated terminal/cancel acknowledgement, without publishing the control
again. A deterministic first-unknown observation and the stale-control negative
case pass locally (three cases). Permanent unknown state still fails the bounded
wait. Runtime heartbeat bounds, control authorization and original deadlines
are unchanged; this is not proof that every unknown outcome is recoverable.

The JavaScript addition is recorded in the
[executable acceptance record](../s3-04-executable-source-acceptance.md).
Its packaged image/source baseline is `a6a95cd`. The following test/CI corrections
give core and shared fixtures distinct module names, preserve the PostgreSQL
job's original Python selection in one `env` mapping and guard against duplicate
workflow keys. The new physical OS-stop cases retain full known/uncertain proof;
their added run time has a larger finite CI aggregate budget, without changing
operation, owner-lease or workflow deadlines. All 8,326 pre-process-addition tests
collect together after the filename correction. No passing checkpoint is inferred
from collection or from the separate image run.

Dependency/SDK changes intentionally change recovery-profile identity. They do
not grant in-place migration of existing invocations. Retained invocations need
their original matching runtime/lock profile; this record authorizes no rollout,
old-runtime removal or publication.

Sources: [FileLock advisory](https://github.com/advisories/GHSA-v4x8-3wr5-3wq8),
[PyPDF advisory](https://github.com/advisories/GHSA-6c7f-9265-g8fw),
[checkpoint advisory](https://github.com/advisories/GHSA-6hmw-9432-67qq),
[OpenTelemetry advisory](https://github.com/advisories/GHSA-xcq7-p3pf-p88h),
[ADK 2.7.1 metadata](https://pypi.org/pypi/google-adk/2.7.1/json).

Local logs, JUnit files, source hashes and both old/corrected advisory reports are
retained under `tmp/completion-6f9ef5d/`. Passing focused selections are not a
whole-source or full-feature qualification.

## Final source 67279ed and source df6b725 follow-up

The final `67279ed` CI outcome is 18 successful jobs and three failures: the
benchmark typing environment and Windows control-observation fixture described
above, plus ADK reaching its 40-minute suite budget at 99% (exit 124). No assertion
failure was reported before that timeout. PostgreSQL, optional Framework,
External Sandbox, Security and Docs all passed. A timed-out suite is not passing
acceptance. The next source uses a finite 45-minute native-suite budget to include
the added physical JavaScript recovery cases; operation and ownership deadlines
remain unchanged.

At `df6b725e4f096df2e361c715c646e64b5f031be1`, optional Framework, External
Sandbox, Security and Docs pass. Both production-image gates pass packaged
JavaScript execution. Root has 5,088 passes, four JavaScript failures, 3,251
skips and 90.11% coverage; LangGraph native has 1,513 passes, four JavaScript
failures and 1,499 skips. Each PostgreSQL operation job has 242 passes, four
JavaScript failures and 183 skips. Both matched benchmark jobs and their
four-cell aggregate pass. CI's remaining jobs retain their own final statuses;
this partial record is not an aggregate green result.

The new JavaScript failures report Linux startup aborts (exit -6), followed by
missing business-commit proof or bounded readiness timeout. A controlled Linux
probe under a dedicated non-root UID reproduces exit -6 with 49 existing user
threads and `RLIMIT_NPROC=32`; Node reports failure creating a startup thread.
The same probe succeeds with quota 128. This supports a shared-UID quota
explanation, but the hosted failure logs do not retain the runner's thread count.
Shared-host JavaScript fixtures now explicitly omit that quota. Production
defaults are unchanged; the packaged image gate asserts the default 32-process
quota and its capability under a dedicated arbitrary UID, with independent
Docker PID/memory limits. Isolated contract runners also require the default
quota through `OWA_TEST_ISOLATED_PROCESS_USER=1`. No secrets, environment or
output controls are relaxed.

Separately, frozen local source `df6b725` passes 19 tests per SDK in Docker
(nine explicit opposite-SDK/platform skips), including both databases, known and
uncertain JavaScript process stops, physical interpreter drift, and both control
observation variants. All 1,139 input hashes remain unchanged. These test images
extend the `a6a95cd` production images; runtime code, locks and metadata match
between those commits. This remains focused local evidence, not an exact-source
production-image rebuild. Reports and the thread-quota probe are retained under
`tmp/javascript-sandbox-20261025/`.
