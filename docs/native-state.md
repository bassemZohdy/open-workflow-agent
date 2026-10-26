# Native state and checkpoint history views

The opt-in LangGraph source profile `langgraph.state.read@1.0.0` exposes bounded
views of existing root checkpoints. It reuses the pinned native saver, with
SQLite as the embedded default and optional PostgreSQL. It creates no second
checkpoint store and performs no native setup or migration during inspection.
This source profile is not yet in a published image. S4-02 remains open.

Mount [the configuration](../examples/native-state.yaml) and [OWS workflow](../examples/native-state-workflow.yaml)
at their configured paths, supply `OWA_NATIVE_STATE_TOKEN` through deployment
secrets, and retain normal `/data` storage. The example uses the fake model and
makes no external model call. Invoke with `{"value":"coffee"}` as `input`, then
use its returned invocation ID:

```http
GET /v1/invocations/{invocation_id}/state
GET /v1/invocations/{invocation_id}/states?limit=20
Authorization: Bearer <deployment token>
```

## Ownership and explicit admission

Select the bootstrap block `native_state: {}` and enable authenticated API
ingress and original execution authorization. The original authenticated owner
must have `state.inspect` under both current permission and retained original
authority. A user/session label or checkpoint reference never grants access.
Another owner's invocation returns the ordinary not-found response.

The workflow must require the exact `langgraph.state.read@1.0.0` capability in
ordinary `document.metadata.owa.nativeExtensions` declarations. Optional/absent
declarations do not permit state inspection. Unsupported required versions are
rejected before API invocation creation. ADK and minimal installations reject
the selected native profile. The official OWS 1.0.3 schema is unchanged.

## Data, references and consistency

Responses contain common invocation/definition identity, public root scope,
opaque checkpoint and parent references, native creation time, initializer/checkpoint
phase, committed root task-visit counters, task data and declared native channel
values. They exclude graph/thread/checkpoint IDs, execution plans, receipt
payloads, routing, counters for internal scopes, variables and context. Task
data can contain sensitive application values; `state.inspect` is separate from
sanitized lifecycle diagnostics and stream permissions.

`state` returns the current retained root checkpoint. Supply its opaque
`reference` query parameter to select a checkpoint inside the available history
window. `states` returns a newest-first page; pass its `next_before` value as
`before` to continue. References bind the original invocation and definition and
remain stable across runtime restarts. They are not native SDK IDs or authority
tokens. Unknown, expired-window or other-invocation selectors return HTTP 409.

The reader takes a native database read snapshot. PostgreSQL uses a read-only
repeatable-read transaction; SQLite opens the existing file in read-only mode,
enforces query-only operation and a read transaction. Missing or incomplete
native storage returns unavailable and is never created/repaired by a read.
The adapter bounds serialized header, blob and pending-write sizes and array
counts before invoking the SDK decoder. Unsupported codecs/versions, corrupt
identity/input/state bindings or immutable common receipts fail closed.

Each projection verifies the archived definition, original input, native state
digest and immutable receipt bindings. An old initializer may be viewed after
later actions; the separate initial-execution admission guard still rejects
replaying that initializer. This read does not verify every child scope or
pending wake, so every response explicitly reports `continuation: not_inspected`.
Inspection never owns execution, dispatches tasks, invokes a model, resumes a
workflow, writes common action evidence or deletes checkpoints.

## Bounds and incomplete history

| Bootstrap setting | Default | Supported bound |
| --- | --- | --- |
| `max_history` | 128 | 1–1,000 recent root checkpoint references |
| `max_page_size` | 20 | 1–100, at most the history window |
| `max_native_bytes` | 16 MiB | 1 KiB–64 MiB serialized native data per read |
| `max_response_bytes` | 1 MiB | 256 bytes–16 MiB projected JSON |
| `max_concurrency` | 4 | 1–32 active reads |
| `timeout_seconds` | 5 | Positive finite seconds, at most 30 |

Concurrent native decoding budgets must fit 256 MiB. Pending native arrays have
a fixed 2,048-row ceiling per checkpoint. These are logical payload/work bounds,
not hard process-memory or isolation guarantees. Full reader capacity returns
HTTP 429 without queuing more native database work. Timeouts and repeated cancellation
retain the reader slot until its database cleanup finishes; draining can exceed
the caller's nominal deadline. Database connection/statement/lock/progress
timeouts supplement the request deadline.

Pages never silently claim complete history beyond the selected window:
`history_window_limited: true` reports older checkpoints outside it. A reference
can leave that window as execution advances. This bounds inspection only;
native checkpoint retention remains engine-owned and no unfinished-run state
is removed. State/history response bytes have a separate ceiling and are never
silently truncated.

These exact references and views underpin subsequent compare-and-update and
branch management. State updates, workflow replay, time travel, other scopes,
full T45/T49 and JavaScript parity retain separate implementation and acceptance
gates in the [state-control acceptance record](s4-02-native-state-acceptance.md).

Configured original-owner updates and interrupted-control resolution use the
separate `langgraph.state.update@1.0.0` profile. Its explicit bootstrap block,
declared schema/task/policy, bounded execution lease and atomic native/common
evidence are described in the [update guide](native-state-updates.md). The read
profile remains observational and never authorizes mutation or continuation.
