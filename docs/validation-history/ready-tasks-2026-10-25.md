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
remain pending until each process exits and its report is verified.

Two initial Windows runs were deliberately stopped: the first used the live
working directory; the second used the frozen source with installed editable
metadata pointing to the live checkout. The latter correctly failed recovery
provenance. Installing the frozen checkout's own locked environment resolves
that mismatch; the three failed environment-smoke cases then pass. These partial
runs and failures are retained, and are not passing qualification evidence.

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
