# S4-02 Native channels: first slice

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

The first slice deliberately excludes value schemas, nested or parallel
writers, Command/Send, memory, streams, state-update APIs, replay, and time
travel. Focused coverage now exercises admission, task-order reduction, reader
visibility, checkpoint privacy, and oversized output rejection. Capability
advertising and task completion remain gated on exact-source hosted and
PostgreSQL validation.
