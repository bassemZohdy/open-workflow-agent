# Declared native stream observations

The opt-in LangGraph source profile `langgraph.streams@1.0.0` observes task data
through the pinned SDK's native `values` and `updates` streams. It requires a
build containing this source; it is not yet in a published runtime image.
Default installations advertise no native-stream capability. The existing
sanitized lifecycle and A2A streams keep their separate contracts.

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

The official OWS schema is unchanged. Optional/absent declarations produce no
native data observations. Unsupported required versions are rejected before
invocation admission; adding deployment settings does not reinterpret existing
workflow definitions. Only the enabled native installation advertises the
capability and `features.nativeStreaming` contract.

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

`mode` selects `values`, `updates` or `all` (default). Other modes are rejected.
Each `native.observation` SSE event contains an immutable JSON envelope with
schema `owa/native-observation/v1`, event ID, invocation ID, mode, public OWS
scope/task reference and task `data`. The adapter excludes its native snapshot,
routing, receipts, counters, variables, context, graph IDs and checkpoints.
Task data can contain sensitive application values; this stream has a different
permission boundary from sanitized lifecycle diagnostics. Messages, debug,
checkpoint, task, custom and JavaScript tool stream modes remain unsupported.

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
