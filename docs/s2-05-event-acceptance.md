# S2-05 embedded event acceptance

Decision: **focused implementation verified; full and configured acceptance pending**.
The task in [TODO.md](../TODO.md) remains open. The embedded event qualification
flag remains false. No entry here closes a backlog checkbox, promotes a full
feature/proposed-test flag or claims the entire user goal is complete.

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

| ID | Requirement and concrete proof | Implemented evidence and current status |
| --- | --- | --- |
| E01 | Embedded inbox/outbox survives complete service reopen; default SQLite and optional PostgreSQL behave equivalently. | [Common store tests](../tests/core/test_event_store.py) reopen the database and preserve canonical envelopes, generated publication values and dedupe. [Runtime composition](../core/src/open_workflow_agent/services.py) defaults to embedded storage. Local SQLite proof passes; actual PostgreSQL qualification pending. |
| E02 | Publication, delivery, selection and common receipts commit atomically; failed transactions leave no acknowledged half-state. | Store failpoints roll back registration, outbox/receipt, selection/final receipt and delivery. [Durable approval tests](../tests/core/test_durable_event_approvals.py) include request and decision transaction participants. Accepted outbox capacity is reserved before acknowledgement. Focus passes; final source and PostgreSQL proof pending. |
| E03 | Complete assigned listener semantics: one/all/any, empty strategies, expression/nested until, filtered candidate contexts, ordered data/envelope/raw/null arrays and sequential foreach item/at/do/output/export. | [Shared durable event fixtures](../tests/contract/test_durable_events.py) cover these results, FIFO order, terminator exclusion, schema failures and nested iterator recovery. [CTK listener fixtures](../tests/ctk/features/listen-reads.feature) use corrected arrays. Focused actual ADK/LangGraph checks pass; final complete suites pending. |
| E04 | Correlation extraction, explicit/omitted expectations and multiple keys persist; mismatches and other trusted scopes do not resume the listener. | Common store immutable progress and shared fixtures bind the first accepted key, reject changed values and recover partial aggregation. Restricted jq evaluates extraction against the envelope and expectations against registration input. Focus passes; actual PostgreSQL cases pending. |
| E05 | Stable source+id duplicate acknowledgement across connections/reopen causes no additional logical arrival/action; conflicts fail closed. | Store tests exercise separate concurrent connections, changed payload rejection, generated-time reuse and cross-source identity. Shared duplicate fixtures check one downstream action after reopened acknowledgement. Scoped principal boundaries are preserved. Local proof passes; configured proof pending. |
| E06 | Early replay, sequential occurrences, concurrent broadcast and terminal late delivery obey the documented embedded policy. | Core cursor-cohort fixtures cover different sequential task references, loops, already registered fork consumers and another invocation. Shared fixtures cover repeated listeners, protected decisions and authorized cancellation. Early events are retained rather than requiring republish. Focus passes; final/PG qualification pending. |
| E07 | Registration precedes waiting observation; selected-event/routing evidence survives native checkpoint gaps with one logical continuation. | [Restart fixtures](../tests/contract/test_durable_events.py) stop at waiting, selected and partial-all boundaries, then reopen actual SDKs; common/native links are reconciled before dispatch. Saved routing receipts prevent cursor/correlation mutation. Focus passes; configured recovery proof pending. |
| E08 | Actual process kill without cleanup recovers waiting, selected, partial-all and known foreach outcomes; legacy scalar state fails admission. | [Forced process-stop fixtures](../tests/contract/test_durable_event_process_recovery.py) kill the worker and reopen after real ownership expiry using deterministic fake models. Both principal SQLite SDK runs pass all five boundaries. PostgreSQL process execution pending. |
| E09 | Identity/fingerprint/occurrence, ownership and complete recovery evidence are enforced before parent mutation, including terminal child dependencies. | Store and shared tests delete/change subscriptions, selections, outboxes, profiles and receipts; stale leases fail. Child common dependencies are traversed recursively. JSON evidence distinguishes booleans from numeric one. Native pre-admission failures preserve parent state and call counts. Focus passes; final/PG proof pending. |
| E10 | Authorized cancellation, terminal status and timeout/termination prevent late resumption; process interruption remains resumable. | Core disposition tests distinguish raw service/process interruption from persisted cancellation. Engine cancellation closes pending subscriptions after terminal commit; selection checks terminal state. Shared cancellation and existing timeout/flow regressions remain required. Focused disposition proof passes; complete lifecycle regression pending. |
| E11 | Approval state remains separate and authoritative; generic decisions cannot be forged or misrouted. | Approval request/decision and inbox changes share the transaction. Original request scope persists across reopen. [Approval guard fixtures](../tests/contract/test_durable_events.py) reject regex lookalikes and unbacked protected events. The decorator preserves delegate durability. Focus passes; full/configured approval regression pending. |
| E12 | Size, storage, queue/scan and correlation limits are enforced without silently discarding acknowledged evidence; errors and principal boundaries remain safe. | [API fixtures](../tests/core/test_durable_event_api.py) reject excess/forged input and payload identity overrides. Store tests check bounds, retained/reserved bytes, pinned retention and atomic failures. PostgreSQL transaction-scoped writer ordering has a real delayed-commit/lock-wait fixture; it is locally skipped without PostgreSQL. Final configured proof pending. |
| E13 | Capability/deployment claims match actual configured behavior and executed evidence; broader staged flags remain open. | Embedded/memory bootstrap and unsupported-backend admission are tested. [Capabilities](../core/src/open_workflow_agent/event_capabilities.py) retain the explicit false qualification gate. [Semantics](durable-events.md) defines duplicate, broadcast, retention, polling and legacy policy. Final metadata/capability review pending; flag remains false. |
| E14 | The frozen final source passes full root/ADK/LangGraph/optional AF, coverage, quality, distributions/schema bytes, Windows, actual PostgreSQL, principal images/restarts, benchmarks and six security reports. | Focused results below do not establish E14. First full runs were stopped for a remaining legacy admission oracle and retained only as partial logs. Corrected-source refreeze, all four complete runs, builds and exact published seven-gate qualification remain pending. No release/merge/deployment is part of this requirement. |

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

Only then may the task, stage record and qualification flag be updated together.
Full feature/proposed/JavaScript claims, compatible migration, durable timers,
external brokers and broader lifecycle history retain their later owners. The
complete backlog remains the user goal.
