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

Use this endpoint to discover the selected engine and supported portable/optional features. Do not assume that every engine exposes identical optional features.

When enabled, `features.catalogs` reports the external-catalog trust mode and resolved function references without returning catalog endpoints, credentials, or remote definitions. Readiness is not reported until configured catalog functions have been fetched and verified.

When the sandbox is enabled, `features.sandbox` reports the selected backend and only the controls that backend actually enforces, for example:

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

`sandbox.enabled: false` (the default) means `run.shell`, `run.script`, and `run.container` are rejected. See [sandbox-execution.md](sandbox-execution.md) for the per-backend capability model.

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

The endpoint returns a bounded CloudEvents JSON batch snapshot with media type:

```text
application/cloudevents-batch+json
```

The current CloudEvents baseline is pinned in [protocol-baselines.md](protocol-baselines.md). This endpoint is a snapshot, not a stream and not a durable event broker.

### Lifecycle SSE stream

```http
GET /v1/events/lifecycle/stream
```

A bounded Server-Sent Events stream of the same lifecycle CloudEvents, advertised through `features.lifecycleStreaming`. It is a bounded transport, not a durable streaming contract:

```text
max_events       default 100 (1-1000)     stream terminates after this many events
max_bytes        default 1048576 bytes    stream terminates after this many bytes
timeout_seconds  default 30 (0-300]       stream terminates after this long
queue_size       default 64 (1-1000)      bounded buffer; overflow terminates the stream
Last-Event-ID    optional header          resume from that event; unknown IDs return 409
```

The stream always terminates and carries lifecycle events only — it is not general output/token streaming. Concurrent streams are capacity-limited; over capacity returns HTTP `429` (`stream_capacity_exceeded`).

## Approvals (human-in-the-loop)

Durable approval state is a bounded HITL mechanism layered on the standard event contract. It is disabled until the deployment sets:

```yaml
approvals:
  enabled: true
  operator_token: <deployment-provided bearer token>
```

The existing approval-specific bearer field remains a bounded pre-security-profile implementation. The shared security-profile backlog will externalize common auth/authz policy without changing the rule that credentials are deployment configuration.

### Workflow side

A workflow requests approval with a standard `emit` task and waits with the standard `listen` task using a deterministic `one.with` filter. The request event's CloudEvents extension `approvalexpiresat` (optional) sets decision expiry. Event `data` is untrusted input: the workflow's input/output schema must validate a decision before it affects a side effect.

### Operator endpoints

All approval endpoints require the configured bearer token **and** an operator identity:

```text
Authorization: Bearer <approvals.operator_token>
X-Operator-Id: <operator identity>
```

List the inbox:

```http
GET /v1/approvals?status=pending&limit=100
```

Read one record:

```http
GET /v1/approvals/{approval_id}
```

Respond:

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

Records persist across restarts. Once a decision is terminal, it replays through the normal `listen` path, so a waiting invocation resumes with the decision after a restart.

## Inbound A2A (optional, bounded)

The runtime can expose itself as an A2A agent. The current implementation targets stable A2A release **1.0.1** and advertises protocol version **1.0**. It is disabled by default.

Current deployment configuration remains:

```yaml
a2a:
  enabled: true
  transport: jsonrpc        # jsonrpc | http_json
  path: /a2a
  agent_name: Open Workflow Agent
  public_base_url: https://agents.example.com
  auth_token: set-via-deployment-secret   # temporary bounded bearer field
  max_message_chars: 100000
```

`auth_token` will be replaced by the shared named security-profile model tracked in `TODO.md`. Authentication/authorization remains deployment configuration; raw credentials must not be placed in workflow definitions.

### Discovery

A2A v1 Agent Card discovery:

```http
GET /.well-known/agent-card.json
```

The card uses `supportedInterfaces[]`; each active interface declares:

```json
{
  "url": "https://agents.example.com/a2a",
  "protocolBinding": "JSONRPC",
  "protocolVersion": "1.0"
}
```

OWA intentionally does **not** retain the legacy v0.3 discovery paths `/a2a/agent.json` or `/.well-known/agent.json`.

### JSON-RPC binding

With `a2a.transport: jsonrpc`:

```http
POST /a2a
Content-Type: application/json
```

Bounded request example:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {
      "role": "ROLE_USER",
      "messageId": "m-1",
      "parts": [
        {"text": "hello"}
      ]
    }
  }
}
```

The bounded profile supports `SendMessage` only. Legacy v0.3 `message/send` and `part.kind` forms are rejected rather than maintained as compatibility aliases.

### HTTP+JSON binding

With `a2a.transport: http_json`:

```http
POST /a2a/message:send
Content-Type: application/a2a+json
```

Request:

```json
{
  "message": {
    "role": "ROLE_USER",
    "messageId": "m-1",
    "parts": [
      {"text": "hello"}
    ]
  }
}
```

The response uses `application/a2a+json` and returns the bounded SendMessage response shape containing an agent message.

### Current A2A capability boundary

`/v1/capabilities` reports the pinned spec release/protocol version and exact bounded features. Current status:

```text
Agent Card discovery        implemented
SendMessage                 implemented
persistent A2A Tasks        not yet implemented
Task get/cancel             not yet implemented
streaming/resubscription    not yet implemented
push notifications          deferred
full conformance claim      not claimed
```

The next A2A work maps A2A Tasks onto common OWA invocation/ExecutionHandle state instead of introducing a second persistence or execution engine. See [protocol-security-decisions.md](protocol-security-decisions.md), [protocol-baselines.md](protocol-baselines.md), and [a2a-streaming-evaluation.md](a2a-streaming-evaluation.md).

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

`user_id` is not an authenticated principal. Authentication and authorization are deployment/runtime configuration and are being consolidated into reusable named security profiles.

The initial shared security profile types are intentionally limited to the common mechanisms:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Authorization terminology is standardized as principal/identity, role, scope, permission/action, resource, and audience. Traffic limits are a separate `traffic_policy` concern rather than part of the security profile.

Treat workflow files as trusted deployment artifacts and invocation/event input as untrusted data. Workflows must not contain raw credentials.
