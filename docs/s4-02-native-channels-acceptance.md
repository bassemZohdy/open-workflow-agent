# S4-02 Native channels and bounded Command routing

Status: implementation authored; S4-02 remains open. The focused core
admission/normalization tests pass (10 cases), as do the native LangGraph
channel tests (3 cases). A real process-stop regression now exercises this
extension on LangGraph with SQLite and PostgreSQL: the producer commits one
reducer delta, reaches a durable wait, and is killed; a new process resumes the
invocation, checks the final reduced value contains the delta once, and verifies
a duplicate terminal resume does not change the result. The SQLite selection
passes locally; PostgreSQL and exact-source hosted qualification remain
pending.

The recovery selector is
`tests/contract/test_native_channel_process_recovery.py::test_process_stop_preserves_committed_native_channel_delta`.
The independent in-process reducer/privacy selectors are in
`tests/langgraph/test_native_task_graph.py`.

A further integration regression exercises the `last_value` reducer through
metadata admission, a real writer task, and a later reader task. It confirms
that an object value replaces the declared initial object and is visible
through the selected reader binding. Its focused local selection passes; it
does not qualify other reducers, nested/parallel writers, or the wider S4-02
scope. The run/pause/resume wait-boundary case was included in the consolidated
local batch where runnable; PostgreSQL and exact-source hosted qualification
remain pending.

This slice implements only declared LangGraph channels and reducers under the exact required extension declaration `langgraph.channels@1.0.0`. Its payload is stored at `document.metadata.owa.nativeExtensionData["langgraph.channels@1.0.0"]` and contains only `channels` and `reads`. It does not change the vendored OWS 1.0.3 schema or expose the derived execution plan.

Each channel has `{id,reducer,initial,writes}`. The supported reducers are `append` for JSON arrays and `last_value` for any admitted JSON-safe value. Writers and readers bind only to direct root `/do/<index>` references. Writers are normalized to root task order. A reader receives only its selected channel values through the temporary `$owa_native_channels` expression variable for that task; the variable is removed before snapshotting task state.

LangGraph stores the channel map in a private reducer-backed LangGraph state channel. Node updates are reducer deltas so the graph can apply each node's channel contribution through its native state mechanism. The process-stop regression above covers restart/resume and checks that a committed delta is not duplicated. The channel map is not copied into the OWA execution snapshot or public invocation result. Channel values are bounded to 65,536 compact JSON bytes each and 262,144 aggregate bytes; the same limits are checked before emitting updates and while admitting checkpoint state. Initial values have matching finite caps.

The channel slice deliberately excludes value schemas, nested or parallel
writers, Send, memory, streams, state-update APIs, replay, and time
travel. Focused coverage now exercises admission, task-order reduction, reader
visibility, checkpoint privacy, and oversized output rejection. Capability
advertising and task completion remain gated on exact-source hosted and
PostgreSQL validation.

## Nested-reader privacy and Command candidate — 2026-10-25

A reader on a root compound task previously left its temporary expression
binding in nested native checkpoints. Three regressions first reproduced that
leak for `do`, `for`, and `fork`. Snapshot encoding now removes the binding in
every scope, and decoding rejects a stored binding before recovery mutation.
Nested execution preserves the live caller binding so the parent task's output
transform still reads its declared value. Negative recovery fixtures inject
the binding into actual SDK checkpoints and recompute the state digest; rejection
must preserve complete common rows and invocation-scoped native rows and prevent
a second HTTP dispatch. Both SQLite and PostgreSQL injection cases pass locally.

The exact required declaration `langgraph.command@1.0.0` now selects actual SDK
`Command(update=..., goto=...)` returns for admitted OWS task transitions. There
is no additional public payload. Destinations are restricted to nodes in the
current compiled scope or its end; updates contain only private execution state
and declared reducer deltas. An optional declaration retains the existing routing
path. This candidate does not accept arbitrary host Command objects, parent-graph
routing or cross-scope destinations. It does not implement Send or public state
updates. ADK rejects the required declaration before native storage, common
mutation or provider dispatch.

Four actual-SDK tests cover required/optional declarations and root/nested
transitions, including a jump over an undeployed model call. Two ADK rejection
cases pass. The physical process-stop grid now combines Command/default routing
with root/nested readers on each database. All four Windows SQLite cells pass:
the producer inspects actual checkpoint histories and pending writes for privacy,
is killed, and a different process recovers exactly one committed reducer delta.
PostgreSQL process-stop acceptance and full exact-source qualification remain
required. These focused results do not complete S4-02's wider scope.

The broader local LangGraph/native-state/SQLite process selection has 111
passes and one stale capability-list expectation; that expectation is corrected
and the adapter/Command selection passes all 11 cases. PostgreSQL forged-binding
cases pass again in the final candidate. All 195 CI guard tests pass. Formatting,
runtime typing and documentation links pass; full-source native and hosted checks
remain separate acceptance gates.

## Bounded native Send candidate — 2026-10-25

The exact required declaration `langgraph.send@1.0.0` selects actual SDK `Send`
fan-out for standard non-competing forks with one to 64 branches. Required
competitive/oversized scopes reject during capability admission before business
or native mutation. Optional declarations keep the existing common launch path;
ADK rejects the required capability. No native extension data or public plan
format is added, and no arbitrary node destination, host code or Send payload
is accepted. Packets contain only a private admitted branch index.

Core owns branch state copies, ordered results, end/fault/cancellation handling,
wait/child suspensions and common receipts. An optional framework-neutral scope
launch seam lets the SDK dispatch the original common branch Tasks, preserving
their exact ownership identities. The Send wrapper graph is transient with
checkpointing explicitly disabled; the existing native branch graphs retain
their original durable scope checkpoints and synchronous write fences.
The wrapper does not provide distributed execution or a new recovery authority.

The first integration run found the pinned SDK inheriting synchronous durability
into a graph with checkpointing disabled (`AsyncPregelLoop` lacked
`_put_checkpoint_fut`). The pinned native adapter now sets its private dispatch
configuration to exit durability without inheriting the parent saver. Existing
branch scopes still explicitly use synchronous persistence. The final SDK
selection passes without that warning/failure: 22 adapter/Command/Send cases,
and three actual ADK required-extension admission rejections.

Thirteen focused actual LangGraph cases pass on Windows, including two physical
SQLite/PostgreSQL recoveries. A producer commits its fork action and native wait,
is killed without cleanup, and a different process resumes with original
identity, retained trace/history, fresh span IDs and one fsynced transport-fake
business dispatch. Other cases prove default/required routing, nested scopes,
ordered results, common end/fault draining, admission bounds, startup-failure
cleanup and cancellation. Windows/native and PostgreSQL/native CI selections
explicitly require this file; full-source/Linux qualification remains pending.

A concurrent working-tree default-fork selection reported 138 passes, 26 skips
and one `runtime_profile_changed` recovery rejection while adapter files were
being edited. That run is not acceptance; a frozen-source rerun is required.
This candidate does not complete S4-02: native memory, streams, public state
updates, replay and time travel retain their separate capability/acceptance gaps.

The frozen `bc044fed090b4287010dee796c47cba1e63607d7` Windows default-fork rerun
passes 139 cases with 26 unconfigured PostgreSQL skips, including the previously
rejected competitive-fork recovery cell. No runtime file changed during that
rerun. Its actual LangGraph adapter/Command/Send selection passes 24 cases,
including both database physical recoveries, and the actual ADK fork/native
selection passes 25. Reports are retained under
`tmp/javascript-sandbox-20261025/bc044fe-*.xml`. Linux and full-source gates
remain pending.
