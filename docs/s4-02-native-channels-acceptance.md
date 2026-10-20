# S4-02 Native channels: first slice

Status: implementation authored. The focused core admission/normalization tests
pass (10 cases), as do the native LangGraph channel/recovery tests (3 cases).
The full validation batch and hosted qualification remain pending; S4-02 stays
open.

This slice implements only declared LangGraph channels and reducers under the exact required extension declaration `langgraph.channels@1.0.0`. Its payload is stored at `document.metadata.owa.nativeExtensionData["langgraph.channels@1.0.0"]` and contains only `channels` and `reads`. It does not change the vendored OWS 1.0.3 schema or expose the derived execution plan.

Each channel has `{id,reducer,initial,writes}`. The supported reducers are `append` for JSON arrays and `last_value` for any admitted JSON-safe value. Writers and readers bind only to direct root `/do/<index>` references. Writers are normalized to root task order. A reader receives only its selected channel values through the temporary `$owa_native_channels` expression variable for that task; the variable is removed before snapshotting task state.

LangGraph stores the channel map in a private reducer-backed LangGraph state channel. Node updates are reducer deltas, so a resumed graph does not reapply already committed node writes. The channel map is not copied into the OWA execution snapshot or public invocation result. Channel values are bounded to 65,536 compact JSON bytes each and 262,144 aggregate bytes; the same limits are checked before emitting updates and while admitting checkpoint state. Initial values have matching finite caps.

The first slice deliberately excludes value schemas, nested or parallel
writers, Command/Send, memory, streams, state-update APIs, replay, and time
travel. Focused coverage now exercises admission, task-order reduction, reader
visibility, checkpoint privacy, and oversized output rejection. Capability
advertising and task completion remain gated on the final batch validations.
