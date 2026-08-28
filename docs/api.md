# HTTP API Guide

Open Workflow Agent exposes an engine-neutral HTTP API. Clients do not need ADK run IDs, LangGraph thread IDs, or checkpoint IDs.

Examples below use `http://localhost:8080`.

## Health

### Liveness

```http
GET /health/live
```

Response:

```json
{"status":"ok"}
```

### Readiness

```http
GET /health/ready
```

Ready:

```json
{"status":"ok"}
```

Not ready returns HTTP `503`:

```json
{"status":"not_ready"}
```

## Capabilities

```http
GET /v1/capabilities
```

Use this endpoint to discover the selected engine and supported portable/optional features.

Do not assume that every engine exposes identical optional features.

When enabled, `features.catalogs` reports the external-catalog trust mode and
resolved function references without returning catalog endpoints, credentials,
or remote definitions. Readiness is not reported until configured catalog
functions have been fetched and verified.

When the sandbox is enabled, `features.sandbox` reports the selected backend and
only the controls that backend actually enforces, for example:

```json
{
  "features": {
    "sandbox": {
      "enabled": true,
      "backend": "internal",
      "internalProcess": {"enabled": true, "shell": {"enabled": false}}
    },
    "approvals": {
      "approval": true,
      "durable": true,
      "replay": true,
      "operatorAuthorization": "bearer"
    },
    "lifecycleStreaming": {"enabled": true, "transport": "sse", "durable": false}
  }
}
```

`sandbox.enabled: false` (the default) means `run.shell`, `run.script`, and
`run.container` are rejected. See [sandbox-execution.md](sandbox-execution.md)
for the per-backend capability model.

## Invoke a workflow

```http
POST /v1/invoke
Content-Type: application/json
```

Request:

```json
{
  "user_id": "u123",
  "session_id": "s456",
  "input": {
    "question": "How can I renew my license?"
  }
}
```

Only `input` is normally needed. `user_id` and `session_id` are optional.

`user_id` is correlation/application identity data. Supplying it does not itself authenticate the request.

Successful response shape:

```json
{
  "invocation_id": "...",
  "session_id": "...",
  "status": "completed",
  "output": {}
}
```

Possible lifecycle states may include:

```text
running
waiting
suspended
completed
faulted
cancelled
```

A faulted invocation returns an engine-neutral error payload.

## Resume an invocation

```http
POST /v1/invocations/{invocation_id}/resume
Content-Type: application/json
```

Request:

```json
{
  "input": {
    "approved": true
  }
}
```

The runtime resolves the engine-specific durable state internally.

Resume verifies that the current workflow definition still matches the fingerprint stored with the invocation. A changed workflow cannot be silently resumed against old state.

## Cancel an invocation

```http
POST /v1/invocations/{invocation_id}/cancel
```

For idempotent cancellation, optionally send:

```http
Idempotency-Key: operation-123
```

## Knowledge reload

```http
POST /v1/admin/knowledge/reload
```

Use this when knowledge reload mode is `manual`, or when you want to trigger reconciliation explicitly.

## Publish an event

```http
POST /v1/events
Content-Type: application/json
```

Request:

```json
{
  "event": {
    "type": "example.event",
    "data": {
      "value": 1
    }
  }
}
```

Current generic event delivery is process-local and non-durable. It should not be treated as a durable broker or approval queue.

## Lifecycle events

```http
GET /v1/events/lifecycle?limit=100
```

The endpoint returns a bounded CloudEvents 1.0 JSON batch snapshot with media type:

```text
application/cloudevents-batch+json
```

It is a snapshot, not a stream and not a durable event broker.

### Lifecycle SSE stream

```http
GET /v1/events/lifecycle/stream
```

A bounded Server-Sent Events stream of the same lifecycle CloudEvents, advertised
through `features.lifecycleStreaming`. It is a bounded transport, not a durable
streaming contract:

```text
max_events       default 100 (1-1000)     stream terminates after this many events
max_bytes        default 1048576 bytes    stream terminates after this many bytes
timeout_seconds  default 30 (0-300]       stream terminates after this long
queue_size       default 64 (1-1000)      bounded buffer; overflow terminates the stream
Last-Event-ID    optional header          resume from that event; unknown IDs return 409
```

The stream always terminates (terminal event, bound reached, or timeout) and
carries lifecycle events only — it is not general output/token streaming.
Concurrent streams are capacity-limited; over capacity returns HTTP `429`
(`stream_capacity_exceeded`).

## Approvals (human-in-the-loop)

Durable approval state is a bounded HITL mechanism layered on the standard
event contract. It is disabled until the deployment sets:

```yaml
approvals:
  enabled: true
  operator_token: <deployment-provided bearer token>
```

### Workflow side

A workflow requests approval with a standard `emit` task and waits with a
standard `listen` task using a deterministic `one.with` filter. The request
event's CloudEvents extension `approvalexpiresat` (optional) sets decision
expiry. Event `data` is untrusted input: the workflow's input/output schema must
validate a decision before it affects a side effect.

### Operator endpoints

All approval endpoints require the configured bearer token **and** an operator
identity:

```text
Authorization: Bearer <approvals.operator_token>
X-Operator-Id: <operator identity>
```

List the inbox (optionally filtered by `status=pending|approved|rejected|expired`):

```http
GET /v1/approvals?status=pending&limit=100
```

Read one record:

```http
GET /v1/approvals/{approval_id}
```

Respond (idempotent per `Idempotency-Key`; repeated decisions on a terminal
approval are rejected):

```http
POST /v1/approvals/{approval_id}/decision
Authorization: Bearer <token>
X-Operator-Id: alice
Idempotency-Key: decision-123
Content-Type: application/json

{
  "decision": "approved",
  "value": {"comment": "looks good"}
}
```

Records persist across restarts. Once a decision is terminal, it replays
through the normal `listen` path, so a waiting invocation resumes with the
decision after a restart.

The bearer/operator guard is a bounded deployment authorization boundary, not a
replacement for an enterprise identity provider.

## Inbound A2A (optional, bounded)

The runtime can expose itself as an A2A agent. This is disabled by default and
gated by deployment configuration:

```yaml
a2a:
  enabled: true
  transport: jsonrpc        # jsonrpc (default, most deployed) | http_json
  path: /a2a
  agent_name: Open Workflow Agent
  auth_token: set-via-deployment-secret   # optional bearer
  max_message_chars: 100000
```

Two transport implementations are selectable through `a2a.transport`:

- `jsonrpc` (default) — JSON-RPC 2.0 over HTTP, the most widely deployed A2A
  transport. `message/send` only; JSON-RPC error objects carry sanitized codes.
- `http_json` — the A2A HTTP+JSON transport: the body is a plain A2A message
  object and the reply is an agent message object.

Discovery:

```http
GET /a2a/agent.json          (also at /.well-known/agent.json)
```

returns the bounded Agent Card (`preferredTransport`, capabilities without
streaming or push notifications, one skill bound to the configured workflow).

`message/send` is synchronous: the first text part becomes the workflow input
(`question`), and the workflow output text becomes the reply's text part. When
the workflow is waiting, cancelled, or faults, the transport returns a
sanitized error (`workflow_waiting`, `invocation_cancelled`, or the common
error code) — there is no long-lived task object in this profile.

When `auth_token` is set, every A2A request requires
`Authorization: Bearer <token>` (HTTP 401 otherwise). `/v1/capabilities`
reports the active block under `features.a2a`. Streaming (`message/stream`),
push notifications, and persistent tasks are intentionally not part of this
profile; see [a2a-streaming-evaluation.md](a2a-streaming-evaluation.md).

## Schedules

### Create

```http
POST /v1/schedules
Content-Type: application/json
```

Request:

```json
{
  "input": {
    "job": "example"
  }
}
```

Optionally use:

```http
Idempotency-Key: schedule-operation-123
```

The workflow itself defines the supported scheduling semantics.

### Get

```http
GET /v1/schedules/{schedule_id}
```

### Cancel

```http
POST /v1/schedules/{schedule_id}/cancel
```

An `Idempotency-Key` header is also supported for schedule cancellation.

Current scheduling is intentionally bounded. Durable `after` and `every` starts are supported; cron, distributed scheduler ownership, and event-triggered scheduling are not currently claimed.

## Error format

Runtime errors use a common envelope:

```json
{
  "error": {
    "code": "...",
    "message": "...",
    "details": {}
  }
}
```

Validation errors return HTTP `422` with the same top-level `error` shape.

Oversized bounded requests return HTTP `413`:

```json
{
  "error": {
    "code": "request_too_large",
    "message": "request body exceeds 1048576 bytes",
    "details": {
      "max_request_bytes": 1048576
    }
  }
}
```

## Security notes

The current HTTP layer does not make `user_id` an authenticated principal. Authentication/authorization should be enforced by the deployment boundary or an appropriate future runtime security layer.

Treat workflow files as trusted deployment artifacts and invocation/event input as untrusted data.
