# Project status audit and qualification — 2026-11-19

The backlog remains **31 of 37 verified tasks**, with S4-02/S4-03/S4-04 ready
and S4-09/S4-10/S4-11 conditional. None is deferred. This audit does not close
an unchecked task or promote a proposed feature/schema criterion.

## Native stream batch

Source `ba6d767c2f9ca9965b2ce289d11714cb85bfd098` adds at most two direct
literal loops per protected body while preserving the existing loop/nesting
limits. Its [component record](../s4-02-native-streams-acceptance.md) retains
the original admission-bypass probes, interrupted formatting attempt,
100-case Windows selection, 600-case frozen Linux selection, all 62 hosted
witnesses and the source manifest. The finite component evidence does not
establish full S4-02 or release readiness.

The frozen first qualification uses
`codex/s4-02-protected-siblings-ba6d767`:

| Workflow | Run | Observed outcome |
| --- | --- | --- |
| CI | [44535332429](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44535332429) | Failed; Windows scoped-flow watchdog failure described below. |
| PostgreSQL CI | [44535332360](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44535332360) | Failed; native-stream selection hit its aggregate limit without a complete report, described below. |
| Optional Framework | [44535332355](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44535332355) | Passed. This does not activate production support. |
| External sandbox | [44535333587](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44535333587) | Failed before contracts: missing optional PostgreSQL driver during type checking; dependent image checks skipped. |
| Security | [44535332991](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44535332991) | Failed five environments on fixable dependency advisories; both controller environments passed. |
| Docs | [44535332114](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44535332114) | Passed. |

## Corrections for a replacement frozen batch

The 439-case native-stream job reached its 25-minute aggregate command limit
(exit 124), after its 65% progress line and a further partial line. The forced
stop left no complete JUnit report, so no completion count is credited. The
replacement preserves the entire cross-store selection, all 62 named witnesses,
zero-skip assertion and individual test bounds. It gives the whole command
45 minutes and its enclosing job 50 minutes, informed by the successful local
600-case run's measured 2510.06 seconds. This is a bounded validation allowance,
not a changed runtime deadline or a performance acceptance claim.

The external-sandbox type-check command now selects the locked `postgres`
extra, matching the existing main CI command. Package-based strict checking
passes 196 source files and all nine workflow guards pass. This changes the
CI analysis environment; PostgreSQL remains optional at runtime.

The Windows expression/admission lane passed 693 cases, skipped 52 and failed
the non-competing fork/end witness on its five-second whole-helper watchdog.
A local diagnostic failed the ADK version while hashing cold source files;
the remaining three variants passed. The witness now performs bounded physical
source verification before its unchanged five-second fork watchdog, following
existing authority/timer test setup. Cancellation, output, task-reference,
event-order and no-later-task assertions remain intact. The complete 62-case
scoped-flow selection passes locally.

The independent lockfiles retain the native engine SDK versions and update
affected dependency versions:

| Dependency | Corrected locked version | Advisory evidence |
| --- | --- | --- |
| LiteLLM | 1.101.3 | [GHSA-7hp6-4w63-5g45](https://github.com/advisories/GHSA-7hp6-4w63-5g45) |
| OpenTelemetry API | 1.45.2 | [GHSA-mf5v-w3w3-f6cg](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg) |
| OpenTelemetry SDK, where selected | 1.45.2, paired with the API | [Package release](https://pypi.org/project/opentelemetry-sdk/1.45.2/) |
| pyasn1 | 0.6.5 | [GHSA-2qm8-x737-gmw5](https://github.com/advisories/GHSA-2qm8-x737-gmw5) and three other scanner findings fixed by the same release |
| pypdf | 6.21.4 | [GHSA-5849-834g-hvw5](https://github.com/advisories/GHSA-5849-834g-hvw5) and the remaining scanner findings fixed by the same release |
| Authlib, ADK only | 1.9.9 | [GHSA-c73q-w75p-j82v](https://github.com/advisories/GHSA-c73q-w75p-j82v) and the remaining Authlib findings |
| joserfc, ADK only | 1.7.7 | [GHSA-9pg5-8c7x-5hfx](https://github.com/advisories/GHSA-9pg5-8c7x-5hfx) |

All seven exported locked environments pass the unchanged strict scanner and
repository audit policy. The first local ADK audit's nine additional Authlib/
joserfc findings remain retained; its corrected report passes separately.
Reports are under `tmp/status-20261119-security/`; the other six reports are
unchanged. No advisory is ignored or waived.

With the patched root dependencies, 239 model/provider, knowledge,
observability, credential, scoped-flow and CI checks pass. The initial seven
skips comprise six PostgreSQL provider-proof cells and one missing-LiteLLM
scenario that intentionally does not run when the SDK is installed. All six
PostgreSQL cells pass on a separate fresh database. The actual ADK/native
selection passes 43 cases with zero skips; 35 other-engine cases are deselected.
The reports are `tmp/status-20261119-patched-dependencies.xml`,
`tmp/status-20261119-provider-postgres.xml` and
`tmp/status-20261119-patched-adk.xml`.

All seven explicit lock checks and 52 CI/tracking guards pass. Repository-wide
lint and formatting pass, as does strict package-based typing over 196 source
files. Both root and standalone-core wheel and source distributions build.

The full corrected source still requires replacement hosted qualification.
The failed first runs are retained and do not qualify the corrected source.

## Replacement frozen qualification — `8d791dc`

Corrected source is frozen at
`8d791dc9d8aa189d77ad06a5b346ee0809086b4a` on
`codex/s4-02-qualified-batch-8d791dc`. All replacement runs select that exact
commit. Their observed state is recorded separately from the failed first
attempt. Outcomes were checked after all runs finished.

| Workflow | Run | Observed outcome |
| --- | --- | --- |
| CI | [44540504116](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44540504116) | Cancelled overall: root coverage failed; Windows expression/admission job reached its 20-minute job limit. Twenty other jobs passed. |
| PostgreSQL CI | [44540503978](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44540503978) | Passed all 19 jobs, including 439 native-stream cases with zero skips in 1630.96 seconds. |
| Optional Framework | [44540504049](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44540504049) | Passed; production support remains conditional. |
| External sandbox | [44540503808](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44540503808) | Passed all four jobs. |
| Security | [44540503746](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44540503746) | Passed all seven environment jobs, with none skipped. |
| Docs | [44540503910](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/44540503910) | Passed. |

## Common boundary follow-up — 2026-11-20

The root suite passed 6613 tests and skipped 3479 in 2023.52 seconds, but its
89.20% core coverage failed the unchanged 90.00% gate (28242 statements,
3050 missed). The root result does not qualify this source. The Windows
expression/admission job was interrupted at its 20-minute aggregate limit;
the retained log contains no failed-test result. No completion count is credited.

The follow-up splits that Windows selection into independent common-service
and shared-admission jobs, each retaining a 20-minute bound. All 19 original
test files remain required exactly once across those jobs; a workflow guard
checks the complete selection. Verbose output and durations aid diagnosis.

New SDK-independent common tests exercise retained catalog memory proofs,
original-owner memory recovery and terminal idempotency, observation ownership,
replay/capacity limits, state query/control boundaries, and damaged state-update
metadata. They use real common storage, authorization and execution ownership
with deterministic provider/controller fakes. They do not qualify native SDK
behavior or change the coverage threshold.

A private bounded credential text projector prepares for the still-missing
native model-message stream. It withholds incomplete literal prefixes, covers
overlapping and fragmented credentials, limits input/output/fragment counts,
and discards raw tails on scope exit, cancellation and binding changes.
It enables no provider path or public streaming capability. Message streaming,
the rest of S4-02, and all later open tasks remain outstanding.

The combined local follow-up passes **415 tests**, with 22 PostgreSQL state
control cells skipped because that process had no database URL. Its report is
`tmp/status-20261120-common-boundaries.xml`. All **22 PostgreSQL cells** then
pass in 18.98 seconds on a separate fresh local database, with zero skips;
`tmp/status-20261120-state-boundaries-postgres.xml` retains that result.
The temporary database container is removed after verification.
The selected common stream API
module reaches 100% statement coverage; the memory recovery, catalog-proof,
state-view and state-control API modules reach 95%, 95%, 98% and 95% respectively.
These component figures do not establish the repository-wide gate. All 788
Python files pass lint/format checks, strict typing passes 197 source files,
and relative Markdown links pass validation.
