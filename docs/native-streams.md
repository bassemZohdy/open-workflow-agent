# Declared native stream observations

The opt-in LangGraph source profile `langgraph.streams@1.0.0` observes task data
through the pinned SDK's native `values` and `updates` streams. It requires a
build containing this source; it is not yet in a published runtime image.
Default installations advertise no native-stream capability. The existing
sanitized lifecycle and A2A streams keep their separate contracts.

The additional `langgraph.streams.tasks@1.0.0` profile opts into native task
lifecycle observations. It requires the base stream profile above and the same
installation, ownership and authorization controls. Without its required
declaration, task events are not collected.

The separate `langgraph.streams.checkpoints@1.0.0` profile observes root
checkpoint references. It requires the base stream profile, the
`langgraph.state.read@1.0.0` profile, and the configured native state reader.
It does not expose native IDs, checkpoint contents or nested-scope checkpoints.

The separate `langgraph.streams.debug@1.0.0` profile observes a bounded,
timestamped projection of native task and root-checkpoint debug events. It
requires the base stream profile. It does not expose native step numbers,
identifiers, task inputs or outputs, exceptions, checkpoint contents or
nested-scope checkpoint events.

The separate `langgraph.streams.custom@1.0.0` profile uses the native SDK's
custom writer for workflow-declared diagnostic events. It requires the base
stream profile. These observations are separate from Open Workflow business
event delivery and do not publish to the event bus.

The separate `langgraph.streams.subgraphs@1.0.0` profile projects native
updates from directly nested `do` scopes. It requires the base stream profile.
It also admits up to three nested `for` tasks, each with a literal `in` array of
at most 32 items and no `while` condition. For nested loops, a conservative
recursive bound on descendant occurrences must not exceed 64 for any loop.
Loop bodies may contain `set`, `call`, `wait`, and permitted inner `for` tasks;
other child scopes inside loops are rejected.
Direct `try` and `catch` bodies, including inside the supported loops, may
contain `set`, `call`, `wait` and `raise` tasks; retry behavior and nested
child scopes inside those bodies are not admitted.
Dynamic or larger loops, deeper child scopes inside a loop and fork child scopes
are rejected at admission. Each event identifies the public child scope and task,
without exposing SDK checkpoint identifiers.

Mount [the configuration](../examples/native-stream.yaml) and [workflow](../examples/native-stream-workflow.yaml)
at the configured paths, provide `OWA_NATIVE_STREAM_TOKEN` through deployment
secrets, and retain the normal SQLite `/data` volume. PostgreSQL is optional.
The example uses the deterministic fake model and makes no external model call.
Invoke with `{"value":"coffee"}` as `input`, then use its returned invocation ID:

```http
GET /v1/invocations/{invocation_id}/stream?mode=all
Authorization: Bearer <deployment token>
Accept: text/event-stream
```

Only the authenticated original owner with explicit `invocations.stream`
permission can read an invocation. Original retained authority and current
permission must both remain valid. An invocation ID, user/session correlation
label or replay cursor never grants access. Another owner's invocation returns
the ordinary not-found response. This read does not acquire execution ownership,
write recovery metadata, invoke the model, dispatch tasks or resume a workflow.

## Deployment and workflow admission

Add `observability.native_streams: {}` to select this data-bearing boundary.
Authenticated API ingress and original execution authorization must be enabled.
ADK and minimal LangGraph installations reject it. A workflow must require the
exact extension through ordinary OWS 1.0.3 metadata:

```yaml
document:
  metadata:
    owa:
      nativeExtensions:
        - name: langgraph.streams
          version: '1.0.0'
          required: true
```

To observe native task lifecycle events as well, require both profiles:

```yaml
document:
  metadata:
    owa:
      nativeExtensions:
        - {name: langgraph.streams, version: '1.0.0', required: true}
        - {name: langgraph.streams.tasks, version: '1.0.0', required: true}
```

The official OWS schema is unchanged. Optional/absent declarations produce no
native data observations. Unsupported required versions are rejected before
invocation admission; adding deployment settings does not reinterpret existing
workflow definitions. Only the enabled native installation advertises the
capability and `features.nativeStreaming` contract.

To include checkpoint observations, explicitly install `native_state: {}` and
add these required declarations alongside the base stream declaration:

```yaml
        - {name: langgraph.state.read, version: '1.0.0', required: true}
        - {name: langgraph.streams.checkpoints, version: '1.0.0', required: true}
```

To include debug observations, require `langgraph.streams.debug@1.0.0` in
addition to the base stream declaration. The debug profile does not require
state-read access and does not emit checkpoint references.

To include supported child updates, require `langgraph.streams.subgraphs@1.0.0`
alongside the base stream declaration. Child updates carry workflow data and use
the same original-owner authorization and bounded retention as other data
observations. Namespace projection accepts up to 32 nested scopes; an invalid
or deeper SDK event ends observation while workflow execution continues.

To include custom observations, require `langgraph.streams.custom@1.0.0` and
declare one to 64 root task bindings in `nativeExtensionData`:

```yaml
document:
  metadata:
    owa:
      nativeExtensions:
        - {name: langgraph.streams, version: '1.0.0', required: true}
        - {name: langgraph.streams.custom, version: '1.0.0', required: true}
      nativeExtensionData:
        langgraph.streams.custom@1.0.0:
          events:
            - {task: /do/0, name: prepared, data: {from: task_output}}
            - {task: /do/1, name: finished, data: {literal: {phase: done}}}
```

Each binding emits after its root task completes. `task` is the root sequence
index, `name` is a lowercase identifier, and `data` selects either the complete
task output or a literal JSON value. Literal strings are not evaluated as jq.
These data-bearing events use the same owner and stream permission boundary as
values and updates; task output may contain sensitive application data. The
emission precedes native checkpoint confirmation, can repeat on recovery, and
is not a durable business event. Oversized data or a failed native writer ends
observation without failing the workflow.

| Deployment setting | Default | Bound |
| --- | --- | --- |
| `max_logs` | 16 | 1–128 retained invocation logs |
| `max_events` | 256 | 1–1,000 retained events per log |
| `max_bytes` | 1,048,576 | 256–16,777,216 retained JSON bytes per log |
| `max_event_bytes` | 65,536 | 256–262,144; must fit the log |
| `max_subscribers` | 16 | 1–128 response slots across the runtime |
| `queue_size` | 32 | 1–1,000 live events per response |
| `retention_seconds` | 60 | Positive finite seconds, at most 600, after an execution segment ends |

Log count × log bytes, response slots × replay bytes, and response slots × queue
size × frame bytes must each fit 64 MiB. These are logical payload ceilings,
not hard process-memory limits. Expiry is checked on access/admission; capacity
evicts finished logs first. Full active capacity skips observation without
faulting business execution. A reader receives an explicit unavailable response.
Responses keep their slot until closed, including after their log is evicted.

## Stream and replay behavior

`mode` selects `values`, `updates`, `tasks`, `checkpoints`, `debug`, `custom`,
`subgraphs` or `all` (default).
Other modes are rejected.
Each `native.observation` SSE event contains an immutable JSON envelope with
schema `owa/native-observation/v1`, event ID, invocation ID, mode, public OWS
scope/task reference and task `data`. The adapter excludes its native snapshot,
routing, receipts, counters, variables, context, graph IDs and checkpoints.
Task data can contain sensitive application values; this stream has a different
permission boundary from sanitized lifecycle diagnostics. A task event contains
only its public scope and task reference and `data: {"status": ...}`. Status is
`started`, `completed`, `faulted` or `interrupted`; SDK task identifiers, input,
output, exceptions, triggers and interrupt payloads are not exposed. These are
observations, not a durable execution journal, and retries or resumed tasks can
produce additional events. A checkpoint event contains only an opaque root
`reference` and no task reference. It can be supplied to the state-read endpoint,
which independently verifies owner, authorization and retained native evidence.
An emitted reference is an observation, not proof of a committed or retained
checkpoint; the read may be unavailable. A debug event carries a timestamp and
either a public task reference with lifecycle status or an opaque root
checkpoint occurrence without a checkpoint reference. A custom event carries
its declared name and selected data with the public task reference. A subgraph
event carries `data: {"mode": "updates", "data": ...}` for a child task.
Messages and JavaScript
tool stream modes remain unsupported.

Events follow native emission order and are observations, not proof that a
checkpoint or business effect committed. The single SDK stream drives normal
workflow execution; readers never start a second graph. A slow reader, invalid
projection or oversized frame ends observation explicitly. It cannot stop or
cancel execution or another observer.

Each response accepts `max_events` (default 100, 1–1,000), `max_bytes` (default
1 MiB, 256–16 MiB) and `timeout_seconds` (default 30, positive finite, at most
300). The deadline also bounds a blocked network sender. A terminal
`stream.end` reports an execution-segment status, timeout, event/byte limit or
observation failure when it fits the byte budget. Disconnecting a reader only
cleans up its observation resources. Subscriber exhaustion returns HTTP 429.

Send the last received ID in `Last-Event-ID` to replay the retained following
interval. Registration and the backlog snapshot share one event-loop operation.
A malformed, evicted, other-invocation or previous-process cursor returns HTTP
409; it never silently restarts at a newer event. This also applies when too
many events were emitted during a resume for the old cursor to remain valid.

Waiting/terminal execution segments finish their live observations while
retaining the bounded history. A same-worker authorized resume extends the same
log incarnation if retained; reconnect to observe that segment. Process exit,
expiry, eviction or another worker can make observations unavailable even when
the invocation and native checkpoint remain recoverable. No observation is
reconstructed by re-executing a workflow. Native workflow replay, time travel,
state updates, full T50 and S4-02 remain separately gated in the [acceptance record](s4-02-native-streams-acceptance.md).
