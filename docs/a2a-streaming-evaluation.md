# A2A and Streaming Evaluation

Status: **bounded inbound A2A 1.0 profile shipped; A2A Tasks and protocol-native streaming remain active backlog.**

## Reference Baseline

The project targets the stable A2A **1.0.1** release and advertises protocol version **1.0** only for implemented/verified behavior.

Authoritative references:

```text
https://a2a-protocol.org/latest/
https://github.com/a2aproject/A2A
```

Legacy A2A v0.3 discovery, methods, and Part forms are intentionally not preserved as compatibility aliases.

## Current Open Workflow Agent A2A Boundary

The runtime now has both:

- a bounded common outbound A2A client path in `ProtocolServices`; and
- an optional bounded inbound A2A server boundary.

Inbound A2A is disabled by default and deployment-controlled.

Implemented discovery/bindings:

```text
GET  /.well-known/agent-card.json
POST <configured A2A path>                 JSON-RPC SendMessage
POST <configured A2A path>/message:send    HTTP+JSON
```

Selectable transports:

```text
jsonrpc    default
http_json
```

Current guarantees:

- stable A2A v1 Agent Card metadata with `supportedInterfaces`;
- synchronous bounded `SendMessage`;
- v1 message/Part shapes;
- deployment-configured public base URL rather than request-derived identity;
- optional temporary deployment bearer guard;
- bounded request/message sizes;
- sanitized transport-specific errors;
- no engine-native checkpoint, thread, run, or stream objects;
- ADK and LangGraph use the same common protocol boundary;
- `/v1/capabilities` advertises only the implemented bounded A2A profile.

Current non-guarantees:

- no persistent A2A Task model yet;
- no Task get/cancel API yet;
- no protocol-native async Task-returning send behavior yet;
- no A2A message/task streaming or resubscription yet;
- no push notifications;
- no broad/full A2A conformance claim;
- no user-delegation/token-exchange contract inside OWA.

## Existing Common Lifecycle SSE

The engine-neutral lifecycle stream is already implemented at:

```text
GET /v1/events/lifecycle/stream
```

It is a reusable runtime primitive, not an A2A binding.

Implemented invariants:

- Server-Sent Events;
- common sanitized lifecycle CloudEvents;
- per-runtime emission ordering;
- subscriber registration before response control is yielded;
- bounded replay with `Last-Event-ID` while the cursor remains retained;
- fail-closed replay error when the cursor is no longer available;
- bounded subscriber queues with slow-consumer termination;
- bounded event count, byte count, stream lifetime, queue size, and subscriber count;
- explicit terminal stream reason;
- disconnecting an observer does not cancel the workflow invocation;
- no ADK/LangGraph checkpoint or native stream objects.

The lifecycle stream remains separate from `features.streaming`, which represents engine-native/general output streaming.

## Why A2A Streaming Is Not the Next First Step

The missing prerequisite is no longer “an inbound A2A endpoint” or “some streaming primitive.” Both now exist in bounded form.

The remaining architectural prerequisite is a portable A2A Task/message/artifact lifecycle projection over common OWA invocation state.

Target relationship:

```text
A2A Task
   |
   +-- task_id / context_id
   +-- status
   +-- messages
   +-- artifacts
   |
   v
OWA invocation_id / ExecutionHandle
   |
   v
ADK or LangGraph native execution state
```

A2A must not introduce a second workflow engine or a second durability model.

Preferred identity:

```text
A2A task_id == OWA invocation_id
```

unless exact A2A 1.0.1 semantics require a distinct external identifier.

## Required Task Mapping Work

The next bounded slice should validate and implement the exact A2A 1.0.1 TaskStatus mapping against the portable OWA lifecycle.

Conceptual mapping only — implementation must validate exact protocol names before finalizing:

```text
A2A submitted / working
        -> common invocation created/running

A2A input-required
        -> common waiting/HITL state

A2A completed
        -> common completed

A2A failed
        -> common faulted

A2A canceled
        -> common cancelled
```

Common invocation, persistence, cancellation, approval, resume, and lifecycle services remain authoritative.

## Recommended A2A Implementation Order

```text
1. Shared named security profiles
2. Deployment-declared A2A skills mapped to registered workflows
3. A2A Task projection over Invocation/ExecutionHandle
4. Task retrieval
5. Task cancellation
6. waiting/input-required/resume mapping
7. spec-native async Task-returning send behavior
8. A2A message/task streaming over common lifecycle events
9. resubscription/reconciliation
10. interoperability/conformance gates
```

This order avoids inventing streaming semantics before the public A2A lifecycle object exists.

## Security Boundary Before Expanding A2A

The current temporary bearer field is a bounded pre-stable implementation detail. The next profile should consume the common named security model.

Initial shared profile types:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Authorization should be expressible per principal/action/resource, including A2A actions such as:

```text
message.send
tasks.get
tasks.cancel
```

A2A skills must be deployment-owned and map only to explicitly registered workflows. A client must never select an arbitrary workflow file/path/catalog entry.

User delegation, OAuth2/OIDC federation, token exchange, and consent remain identity-platform responsibilities and do not block the basic Task projection.

## A2A Streaming Decision

After Task state is stable, A2A HTTP streaming should map protocol-native task/message/artifact updates onto the existing common event/lifecycle infrastructure.

Rules:

- stream incrementally; do not buffer the entire response;
- never expose engine-native checkpoints or stream objects;
- preserve the same authentication/authorization checks as non-streaming operations;
- preserve bounded queues, backpressure, time/byte/event limits, and sanitized errors;
- support resubscription/reconciliation using protocol-native semantics where required;
- capability advertisement must remain exact and fail closed.

The common lifecycle SSE endpoint may provide reusable mechanics, but the A2A stream is a separate protocol contract and must not simply expose raw lifecycle events as if they were A2A Task updates.

## Push Notifications

Push notifications remain deferred because they create a new outbound callback trust boundary.

Required policy before implementation:

- callback endpoint allowlisting;
- TLS/server identity verification;
- callback authentication;
- SSRF protection;
- replay/idempotency protection;
- bounded retries and dead-letter behavior;
- secret-safe logging/observability.

Push notifications are not required to complete the next bounded Task/streaming profile.

## Current Capability Position

```text
common lifecycle SSE          implemented
inbound A2A Agent Card        implemented
A2A SendMessage               implemented
jsonrpc transport             implemented
http_json transport           implemented
persistent A2A Tasks          active backlog
Task get/cancel               active backlog
A2A async Task behavior       active backlog after Task projection
A2A streaming/resubscription  active backlog after Task projection
push notifications            intentionally deferred
full A2A conformance claim    intentionally deferred
```

The key architectural conclusion is:

> The A2A blocker has shifted from transport/server readiness to the missing protocol-level Task projection, authorization boundary, and exact lifecycle mapping. Implement those in core before adding streaming.
