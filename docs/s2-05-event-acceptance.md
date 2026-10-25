# S2-05 embedded event acceptance

Decision: **complete finite S2-05 profile qualified at `58de90e`**.

Current-source regression requalification at `9d4d3ec` later found that the
nondurable-listener test expected a faulted result, while common admission
rejects unsupported capabilities before execution ownership. The regression now
asserts `UnsupportedWorkflowFeature` and unchanged invocation input/state.
Resume performs its read-only native-continuation check before returning the
capability rejection. A first invoke reorder was reverted after Windows gRPC
and PostgreSQL protected-approval gates proved that deployment rejection must
remain before execution ownership. The corrected local event-backend matrix
passes all 72 cases. Exact-source hosted requalification remains pending;
see the [validation history](validation-history/pending-batch-results.md#event-backend-admission-order-follow-up-at-9d4d3ec).
All four frozen local suites and all seven configured workflows passed. The
task in [TODO.md](../TODO.md) is complete after final publication source
`58de90e816a1118252aec507cfd2425f60c2a1bd` passes its exact configured checks. No entry here promotes
a full feature/proposed-test flag or claims the entire user goal is complete.

## Qualified runtime and publication delta

The immutable runtime source is `143d38a44f74bf3d0aa837c48f02d8fe2671fb21`.
All 254 source inputs remain unchanged during the four complete local runs:
core **1,910 passed, 888 skipped, two expected gaps, 91.32% coverage**;
ADK **1,109/688/two**; LangGraph **1,151/661/two**; optional Agent Framework
**1,408/946/two**. The two expected gaps retain S3-03 owners. Five distributions
match source, package/version and unchanged schema bytes. Full lint, 228-file
formatting, strict 73-source typing, changed-fixture typing and 51 audit checks
pass. Skipped PostgreSQL cases in local runs are qualified by actual configured
execution below, rather than inferred from skips.

All seven exact-source workflows succeed: [push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199785274),
[PR CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199789374),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199789380),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199789439),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199789381),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199789379)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38199789378).
Actual PostgreSQL executes **41 durable event cases and five OS process stops
per principal**, **195 core cases**, and the real delayed-commit ordering-lock
test. Both PostgreSQL images execute initial/restarted invocation assertions.
Initial image HTTP logs are unavailable because their exit trap runs after
deliberate initial-container removal; initial success is evidenced by executed
curl/JSON assertions, while restarted HTTP 200 logs are retained.

Both CI runs pass both Windows stages, both principal restart images and native
benchmarks. Downloaded benchmark results confirm actual SDK availability and one
side-effect call across interruption/resume. Six downloaded nonempty nested
security reports contain zero vulnerabilities across 71 root, 109 ADK,
96 LangGraph, 74 optional Agent Framework and 13/16 controller dependencies.
External acceptance executes 55 contracts and both restricted Docker adapters.

The subsequent publication delta changes only the generic event capability
helper, API expectation, image assertion and one typed shared capability matrix.
It qualifies the actual embedded bus and its approval delegate; memory and an
arbitrary custom bus declaring durable=true remain false. Native SPI scope and
CloudEvents snapshot durability are unchanged. Bounded checks pass **73 root,
76 ADK, 76 LangGraph and 111 optional Agent Framework** tests. Runtime execution
bytes remain those of the fully qualified source; the changed publication bytes
and image assertions pass fresh exact-revision configured acceptance below.

The scope is the full S2-05 event contribution: durable inbox/outbox, one/all/any
and until consumption, filter/correlation semantics, ordered array projections,
sequential foreach, duplicate/late policy and restart recovery. The official
schema is unchanged. [Deployment and semantics](durable-events.md) records the
implemented contract and its limits. Timer scheduling and event-trigger-created
invocations remain S2-06; external transport acceptance remains S4-10.

## Finite requirements

Each E01–E14 requirement is mandatory. Focused SQLite/native checks below are
supporting evidence on the working implementation, not substitutes for the
final frozen-source suites or actual configured PostgreSQL execution.
The table records the final finite qualification. Historical focused runs
and earlier failures remain below for provenance; final publication acceptance
also verifies E13/E14, as recorded below.

| ID | Requirement and concrete proof | Implemented evidence and current status |
| --- | --- | --- |
| E01 | Embedded inbox/outbox survives complete service reopen; default SQLite and optional PostgreSQL behave equivalently. | Verified: embedded SQLite reopen and actual PostgreSQL acceptance on both principal SDKs. |
| E02 | Publication, delivery, selection and common receipts commit atomically; failed transactions leave no acknowledged half-state. | Verified: transaction rollback and immutable receipt tests, protected approval participants, reservation limits and configured PostgreSQL. |
| E03 | Complete assigned listener semantics: one/all/any, empty strategies, expression/nested until, filtered candidate contexts, ordered data/envelope/raw/null arrays and sequential foreach item/at/do/output/export. | Verified: identical shared native results for strategies, predicates, projection and foreach; complete suites and actual PostgreSQL pass. |
| E04 | Correlation extraction, explicit/omitted expectations and multiple keys persist; mismatches and other trusted scopes do not resume the listener. | Verified: persisted correlation and trusted scope fixtures pass native SQLite/PostgreSQL recovery. |
| E05 | Stable source+id duplicate acknowledgement across connections/reopen causes no additional logical arrival/action; conflicts fail closed. | Verified: duplicate acknowledgement and changed-payload rejection across independent connections, restart and principal scopes. |
| E06 | Early replay, sequential occurrences, concurrent broadcast and terminal late delivery obey the documented embedded policy. | Verified: documented early replay, sequential cursor cohorts, concurrent broadcast and terminal late-delivery policy. |
| E07 | Registration precedes waiting observation; selected-event/routing evidence survives native checkpoint gaps with one logical continuation. | Verified: waiting, partial selection and routing receipt recovery pass native checkpoints and configured acceptance. |
| E08 | Actual process kill without cleanup recovers waiting, selected, partial-all and known foreach outcomes; legacy scalar state fails admission. | Verified: five actual OS process-stop boundaries per principal on SQLite and PostgreSQL; legacy scalar progress rejects admission. |
| E09 | Identity/fingerprint/occurrence, ownership and complete recovery evidence are enforced before parent mutation, including terminal child dependencies. | Verified: identity, ownership and evidence-corruption fixtures reject before parent mutation or dispatch, including known terminal child dependencies. |
| E10 | Authorized cancellation, terminal status and timeout/termination prevent late resumption; process interruption remains resumable. | Verified: terminal cancellation/disposition and full lifecycle regression; transient process interruption remains recoverable. |
| E11 | Approval state remains separate and authoritative; generic decisions cannot be forged or misrouted. | Verified: atomic protected approvals, authoritative scope and forged-decision guards across complete and configured suites. |
| E12 | Size, storage, queue/scan and correlation limits are enforced without silently discarding acknowledged evidence; errors and principal boundaries remain safe. | Verified: bounded admission/retention/reservation and actual PostgreSQL delayed-commit ordering lock; API principal boundaries pass. |
| E13 | Capability/deployment claims match actual configured behavior and executed evidence; broader staged flags remain open. | Verified at `58de90e`: actual embedded bus and approval delegate advertise durability; memory/custom buses remain false. Live image assertions and native API matrix pass; broader claims retain later owners. |
| E14 | The frozen final source passes full root/ADK/LangGraph/optional AF, coverage, quality, distributions/schema bytes, Windows, actual PostgreSQL, principal images/restarts, benchmarks and six security reports. | Verified: all four frozen local suites, coverage, quality, five distributions and unchanged schema; all seven exact publication workflows, Windows, PostgreSQL, restart images, benchmarks and six nonempty security reports pass. |

## Inspected focused evidence

The following terminal results were inspected on 2026-10-12. They precede the
final source freeze and must not be presented as full-source qualification:

- Core event store: **31 passed, 28 skipped**. PostgreSQL was not configured;
  the extra SQLite variant of the PostgreSQL-only ordering fixture also skips.
  Combined store/existing policy/journal checks previously pass **70/28** before
  the added ordering fixture. Scoped lint and strict four-file typing pass.
- Core event/expression admission: **121 passed** after replacing the stale any
  rejection oracle and adding pre-dispatch syntax checks for correlation,
  until and foreach do/output/export.
- Actual ADK and LangGraph initial integrated focus: **60 passed, 65 skipped**
  each (`s2-05-native-adk-integration-final.log`,
  `s2-05-native-lg-integration-final.log`). These totals do not qualify later
  additions or PostgreSQL cases.
- Actual native process stop: **5 passed, 15 skipped** per principal
  (`s2-05-native-adk-process.log`, `s2-05-native-lg-process-final.log`). The five
  passing SQLite boundaries include historical scalar rejection; skips are the
  other selected engine/unconfigured PostgreSQL variants.
- LangGraph protected-decision/dependency focus: **71 passed, 90 skipped**
  (`s2-05-native-lg-protected-dependencies-final.log`). Backend/admission focus
  subsequently passes **46/2**, with 160 deselected; schema/resource focus
  passes **52** and schema-catch focus **4**, with 40 deselected.
- Optional Agent Framework focus: **156 passed, no skips** in 41.51 seconds
  (`s2-05-native-af-focus-final.log`), using the installed actual native SDK.
  Earlier **153 passed, three failed** exposed the legacy CTK `.published`
  observer. The corrected CTK emission oracle checks committed outbox envelope
  and immutable common receipt; the failing run is not acceptance.
- Seven-module worker/fixture strict typing passes
  (`s2-05-fixture-typing-final.log`). Collection focus passes four S2-05 guards;
  actual collection is selection evidence, not database execution.

Logs named here are local working records; final acceptance must preserve the
reviewed commit, frozen manifest, exact commands and terminal configured links.
The configured PostgreSQL workflow now includes the complete core store file
and the shared/native/process selections. Inspect actual passing PostgreSQL
nodes, including the real delayed-commit ordering test, instead of assuming
that an included path or green collection guard proves database behavior.

## Provenance and remaining gates

Initial published source `d808d36` passes Docs, Security, optional Agent
Framework and external sandbox workflows. Push and PR CI fail a legacy helper
oracle: malformed `listen.all` and empty filter definitions correctly raise a
semantic validation error, while the old assertions expect an unsupported
feature error. Push root records 1 failed, 1,896 passed, 884 skipped, two
expected gaps and 91.31% coverage; Windows records 1 failed, 465 passed and
12 skipped. These failed runs are not qualified acceptance. The follow-up
changes only those two expectations in the core fixture, preserving malformed
definition rejection and the runtime source. Focused workflow/event/admission
checks pass 78 tests; fresh affected full/core and configured checks are required.

The complete local baseline passes ADK 1,105/688/two expected gaps and optional
Agent Framework 1,402/946/two expected gaps. LangGraph records two failures,
1,145 passes, 661 skips and two expected gaps: the optional memory/custom
single-event iterator incorrectly requests a durable selection receipt. The
narrow follow-up identifies that sole item by its existing listener task
occurrence; durable event coordinates and their native receipt guards remain
unchanged. A repeated-listener fixture checks two distinct child calls. These
baseline results remain historical evidence; fresh affected native/full and
configured qualification is required after the runtime correction.

Follow-up `9044943` finishes four successful configured workflows and three
failures. Both CI runs expose the same two native memory/custom iterator
failures. PostgreSQL passes 195 common-store tests, ten native checkpoint
tests, all 41 LangGraph durable cases and five actual event process stops per
principal. ADK passes 40 durable cases but fails the filter-expression case:
retained unrelated scalar payloads reach a dynamic predicate before type and
subject exclude them. Direct matcher probes reproduce the failure on truly
excluded scalar candidates. The correction applies all static restrictions before dynamic
predicates and correlation; legitimate expression faults still propagate.
Thirteen core matcher tests include four prior-source failures. The existing
shared scenario now includes two unrelated scalar arrivals, without hiding
retained history or changing the 41-case PostgreSQL count. Final source
qualification remains pending.

An intermediate strengthened fixture used `other-` plus the allowed subject
UUID as its negative input. That substring still matches the documented
unanchored regular expression, so its scalar payload fault was legitimate.
The final negative uses a different UUID. Intermediate fixture failures do not
provide independent runtime negative proof; the original PostgreSQL trace and
four prior-source core matcher failures do.

Semantic built-in error URIs follow release tag `v1.0.3`, immutable commit
`9b5b1da29e9d4fff2358580241e11aab22704a16`, using
`https://serverlessworkflow.io/spec/1.0.0/errors/`. The task/item schema-validation
URI was aligned with this release policy, and shared catch fixtures retain the
declared standard type, status and output-array assertions. Declared user error
types keep their own URI. See the [release reference](https://github.com/open-workflow-specification/specification/blob/9b5b1da29e9d4fff2358580241e11aab22704a16/dsl-reference.md#standard-error-types).

The official schema remains pinned separately to
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a`. Its reference document uses a later
error URI namespace; floating upstream main is comparison material, not the
runtime's semantic oracle. Both immutable references establish ordered arrays,
sequential FIFO iteration, terminator exclusion and correlation. See
[schema provenance](../resources/open-workflow/1.0.3/SCHEMA_SOURCE.md).

Before closure, record and inspect the four terminal frozen-source suites,
coverage at least 90%, scoped/full configured lint and strict typing, five
wheel/sdist source and unchanged schema checks, both Windows stages, actual
PostgreSQL store/native/process cases, both principal image restart gates,
native benchmarks and all six nonempty zero-advisory dependency reports on
the exact published source. All seven configured workflows must succeed;
cancelled, absent, skipped or earlier-revision results do not qualify a gate.

Only after the publication artifact passes may the task and stage record close.
The capability flag is based on the qualified runtime and explicit backend
matrix above; its publication validation is recorded separately.
Full feature/proposed/JavaScript claims, compatible migration, durable timers,
external brokers and broader lifecycle history retain their later owners. The
complete backlog remains the user goal.

## Final publication acceptance

`58de90e` passes all seven exact workflows: [PostgreSQL CI (pull_request)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705353), [Docs (pull_request)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705287), [Security (pull_request)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705278), [External Sandbox CI (pull_request)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705300), [Agent Framework CI (pull_request)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705286), [CI (pull_request)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705267), [CI (push)](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201700615).
The terminal inspector verifies actual new embedded capability assertions,
Windows stages, both principal image/benchmark pairs, PostgreSQL 41 event
and five process cases per principal, ordering lock, both PostgreSQL images,
optional package and six nonempty nested zero-vulnerability security reports.
Only the verified embedded profile is advertised. All E01-E14 are complete;
14 of 37 active tasks are verified and S2-06 is next. Earlier failures and
broader staged owners remain preserved.

The [first PR CI attempt](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705267/attempts/1) at publication `58de90e` has one Windows native
process-stop fixture failure before its terminal marker: the worker loses its
five-second test ownership lease on that host and correctly fails closed.
It records 647 passes, 478 skips and one failure; the same-source push stage
passes 648/478. Only that failed job is rerun, with no source change. The final
configured decision uses the inspected [retry result](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38201705267/attempts/2); the failed attempt and
its logs remain preserved in the acceptance evidence.
