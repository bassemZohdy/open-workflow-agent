# HTTP API Guide

Open Workflow Agent exposes an engine-neutral HTTP API. Clients do not need ADK run IDs, LangGraph thread IDs, or checkpoint IDs.

Examples below use `http://localhost:8080`.

## Health

```http
GET /health/live
GET /health/ready
```

Liveness returns `{"status":"ok"}`. Readiness returns HTTP `503` with `{"status":"not_ready"}` until runtime initialization is complete.

## Capabilities

```http
GET /v1/capabilities
```

Use this endpoint to discover the selected engine and supported portable/optional features. Optional capabilities are fail-closed and must not be assumed across engines or deployments.

Relevant common blocks include:

- `features.approvals`
- `features.catalogs`
- `features.sandbox`
- `features.lifecycleStreaming`
- `features.a2a`

When A2A is enabled with the current bounded Task profile, the A2A block includes the pinned release/protocol version and advertises only `GetTask`/`CancelTask` as Task operations. Streaming and push notifications remain false.

## Invoke a workflow

```http
POST /v1/invoke
Content-Type: application/json
```

```json
{
  "user_id": "u123",
  "session_id": "s456",
  "input": {"question": "How can I renew my license?"}
}
```

Only `input` is normally needed. `user_id` and `session_id` are optional correlation/application identifiers; `user_id` does not authenticate a caller.

Successful response shape:

```json
{
  "invocation_id": "...",
  "session_id": "...",
  "status": "completed",
  "output": {}
}
```

Common lifecycle states are `running`, `waiting`, `completed`, `faulted`, and `cancelled`.

## Resume an invocation

```http
POST /v1/invocations/{invocation_id}/resume
Content-Type: application/json
```

```json
{"input": {"approved": true}}
```

The runtime resolves engine-native durable state internally. Resume verifies the stored workflow fingerprint before continuing.

## Cancel an invocation

```http
POST /v1/invocations/{invocation_id}/cancel
Idempotency-Key: operation-123
```

The idempotency header is optional.

## Knowledge reload

```http
POST /v1/admin/knowledge/reload
```

## Events

Publish:

```http
POST /v1/events
Content-Type: application/json
```

```json
{
  "event": {
    "type": "example.event",
    "data": {"value": 1}
  }
}
```

Generic event delivery is process-local/non-durable and is not a durable broker or approval queue.

Lifecycle snapshot:

```http
GET /v1/events/lifecycle?limit=100
```

Media type:

```text
application/cloudevents-batch+json
```

Lifecycle SSE:

```http
GET /v1/events/lifecycle/stream
```

Supported bounded controls:

```text
max_events       default 100 (1-1000)
max_bytes        default 1048576
 timeout_seconds default 30 (0-300]
queue_size       default 64 (1-1000)
Last-Event-ID    optional replay cursor
```

This stream carries common lifecycle events only. It is not general output/token streaming and is not itself an A2A binding.

## Approvals (human-in-the-loop)

Durable approval state is disabled until configured:

```yaml
security:
  profiles:
    operator:
      type: bearer
      token:
        from_env: OWA_OPERATOR_TOKEN

approvals:
  enabled: true
  operator_security_profile: operator
```

The approval-specific bearer field remains a bounded pre-shared-security implementation.

Operator endpoints:

```http
GET  /v1/approvals?status=pending&limit=100
GET  /v1/approvals/{approval_id}
POST /v1/approvals/{approval_id}/decision
```

Authorization requires:

```text
Authorization: Bearer <token from the referenced security profile>
X-Operator-Id: <operator identity>
```

A terminal decision persists and replays through the normal workflow `listen` path after restart.

## Inbound A2A (optional, bounded)

OWA can expose itself as an A2A server. The current implementation pins A2A maintenance release **1.0.1** and advertises protocol version **1.0**. Wire behavior follows the official A2A Project definitions at `a2a-protocol.org`.

A2A is disabled by default.

Inbound A2A authentication references a named security profile instead of an inline token:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        from_env: A2A_PARTNER_TOKEN

a2a:
  enabled: true
  transport: jsonrpc        # jsonrpc | http_json
  path: /a2a
  agent_name: Open Workflow Agent
  public_base_url: https://agents.example.com
  security_profile: partner-agent
  skills:
    - id: residence-renewal
      workflow: residence-renewal   # workflow registered via workflow.catalog
  max_message_chars: 100000
```

`a2a.security_profile` must name an entry in `security.profiles` of type `bearer`; the runtime rejects the configuration at startup otherwise. Only bearer-token authentication is wired today.

Per-principal authorization is enforced through `a2a.authorization` rules. The authenticated principal comes from the profile's declared `principal`/`roles`/`scopes`/`audience` attributes, and each operation is checked against the policy vocabulary:

```text
message.send  on skill:<id>   (skill:workflow for the implicit skill)
tasks.get      on tasks
tasks.cancel   on tasks
```

First matching rule allows; no match denies with HTTP `403` (`"forbidden"`).
Without a policy, all authenticated operations are allowed. Declaring an
authorization policy without a security profile fails at startup (`SECURITY-4`).

Declared skills are advertised on the Agent Card and selected by clients
through `message.metadata.skillId`; routing is deployment-owned and unknown
or ambiguous names fail closed (`A2A-3`).

### Discovery

```http
GET /.well-known/agent-card.json
```

The card uses `supportedInterfaces[]` and advertises protocol version `1.0`:

```json
{
  "url": "https://agents.example.com/a2a",
  "protocolBinding": "JSONRPC",
  "protocolVersion": "1.0"
}
```

OWA does not expose the old A2A wire discovery paths `/a2a/agent.json` or `/.well-known/agent.json`.

### JSON-RPC binding

With `a2a.transport: jsonrpc`, all operations use:

```http
POST /a2a
Content-Type: application/json
A2A-Version: 1.0
```

#### SendMessage

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {
      "role": "ROLE_USER",
      "messageId": "m-1",
      "parts": [{"text": "hello"}]
    }
  }
}
```

Blocking sends return `result.message` when the workflow completes, or
`result.task` when the workflow ends up waiting — an A2A Task in
`TASK_STATE_INPUT_REQUIRED` that clients follow with `GetTask`.

Protocol-native asynchronous behavior follows the official
`SendMessageConfiguration`. With `returnImmediately: true` the runtime starts
the invocation and returns the Task projection immediately (typically
`TASK_STATE_WORKING`); the client then follows progress with `GetTask`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
    "configuration": {"returnImmediately": true}
  }
}
```

OWA implements no other async flag.

Resuming sends carry the existing `message.taskId`: a task in
`TASK_STATE_INPUT_REQUIRED` is resumed through the common resume contract
using the message text as input. Unknown task references return `-32001`
(`task_not_found`); terminal or non-waiting tasks are rejected with a
sanitized `task is not accepting input` error. HTTP+JSON maps these to
`404` and `409` respectively.

#### SendStreamingMessage / SubscribeToTask

`SendStreamingMessage` takes the same `params` as `SendMessage` and answers
with `text/event-stream`: an initial Task frame, then official
`statusUpdate`/`artifactUpdate` frames translated from common lifecycle
events, closing at the first terminal state. `SubscribeToTask` takes
`params.id` and streams the same frames for an existing task (a terminal task
yields only its projection). Over HTTP+JSON the routes are
`POST /a2a/message:stream` and `POST /a2a/tasks/{id}:subscribe`.

Streams are bounded — event count, byte size, and duration — and a client
that needs more simply re-subscribes. Disconnecting a stream never cancels
the underlying invocation. Unknown resubscription targets return `-32001`;
resubscription requires the same `tasks.get` authorization as `GetTask`.

#### GetTask

```json
{
  "jsonrpc": "2.0",
  "id": "task-read-1",
  "method": "GetTask",
  "params": {"id": "<invocation-id>"}
}
```

The A2A Task id is the OWA `invocation_id`; `contextId` is the common `session_id`. Engine-native references are never returned.

#### CancelTask

```json
{
  "jsonrpc": "2.0",
  "id": "task-cancel-1",
  "method": "CancelTask",
  "params": {"id": "<invocation-id>"}
}
```

Cancellation is routed through the common engine cancellation API. A terminal completed/faulted/cancelled invocation is not cancelable.

Official bounded JSON-RPC mappings used by OWA:

```text
Task not found       -32001
Task not cancelable  -32002
```

### HTTP+JSON binding

With `a2a.transport: http_json`, use media type:

```text
application/a2a+json
```

Send:

```http
POST /a2a/message:send
A2A-Version: 1.0
Content-Type: application/a2a+json
```

The body is the same A2A `SendMessageRequest` object as the JSON-RPC `params`
(message plus optional `configuration.returnImmediately`), and responses carry
the same `message`/`task` bodies described for JSON-RPC above.

Get a Task:

```http
GET /a2a/tasks/{task_id}
A2A-Version: 1.0
```

Cancel a Task:

```http
POST /a2a/tasks/{task_id}:cancel
A2A-Version: 1.0
```

Missing Tasks return HTTP `404`; non-cancelable Tasks return HTTP `400`.

Stream and resubscribe use the same semantics as the JSON-RPC
`SendStreamingMessage`/`SubscribeToTask` operations:

```http
POST /a2a/message:stream
POST /a2a/tasks/{task_id}:subscribe
A2A-Version: 1.0
```

Both answer `text/event-stream` and are bounded like every other stream.

### Task projection

OWA does not maintain a second A2A persistence engine. Task state is projected from common invocation state:

| Common invocation | A2A Task state |
| --- | --- |
| `running` | `TASK_STATE_WORKING` |
| `waiting` | `TASK_STATE_INPUT_REQUIRED` |
| `completed` | `TASK_STATE_COMPLETED` |
| `faulted` | `TASK_STATE_FAILED` |
| `cancelled` | `TASK_STATE_CANCELED` |

Completed output becomes a bounded Task Artifact:

- strings -> `text` Part;
- JSON-compatible objects -> `data` Part;
- bytes -> base64 `raw` Part.

Waiting/failure status messages include Task/context identifiers. Failure output exposes only a sanitized common error code.

### Open Workflow calls vs A2A wire methods

Open Workflow 1.0.3 defines schema-level A2A call values such as `message/send`, `tasks/get`, and `tasks/cancel`. OWA preserves that official workflow schema and translates those values at the runtime protocol boundary to `SendMessage`, `GetTask`, and `CancelTask`.

Therefore `message/send` may validly appear in an **Open Workflow document**, while the external A2A JSON-RPC endpoint still rejects it as a wire method.

### Current A2A capability boundary

```text
Agent Card discovery          implemented
SendMessage                   implemented
Task projection               implemented
GetTask                       implemented
CancelTask                    implemented
per-principal authorization   implemented (a2a.authorization)
multi-skill routing           implemented (a2a.skills)
returnImmediately async       implemented
waiting/resume A2A mapping    implemented over the common resume contract
streaming/resubscription      implemented (bounded SSE, A2A-7)
push notifications            deferred
full conformance claim        not claimed
```

See [a2a-streaming-evaluation.md](a2a-streaming-evaluation.md), [protocol-baselines.md](protocol-baselines.md), and [protocol-security-decisions.md](protocol-security-decisions.md).

## Schedules

Create:

```http
POST /v1/schedules
Content-Type: application/json
```

Get:

```http
GET /v1/schedules/{schedule_id}
```

Cancel:

```http
POST /v1/schedules/{schedule_id}/cancel
Idempotency-Key: schedule-operation-123
```

Durable `after` and `every` starts are supported. Cron, distributed scheduler ownership, and event-triggered scheduling are not claimed.

## Error format

Common runtime errors use:

```json
{
  "error": {
    "code": "...",
    "message": "...",
    "details": {}
  }
}
```

Validation errors return `422`. Oversized bounded requests return `413` with `request_too_large`.

A2A transport errors follow the bounded A2A binding-specific response/error shape rather than the ordinary OWA `/v1/*` envelope.

## Security notes

`user_id` is correlation data, not an authenticated principal.

Framework-neutral named-security primitives now exist for:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

They use deployment environment references for sensitive values and hide rejected validation inputs. Runtime configuration/protocol adapter integration remains active work.

Authorization vocabulary is standardized as principal/identity, role, scope, permission/action, resource, and audience. Traffic rate/concurrency policy remains a separate concern.

Treat workflow files as trusted deployment artifacts and invocation/event input as untrusted data. Workflows must not contain raw credentials.
