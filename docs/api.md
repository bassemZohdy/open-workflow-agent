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
