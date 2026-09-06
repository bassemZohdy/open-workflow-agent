# A2A and Streaming Evaluation

Status: **bounded inbound A2A v1 profile fully implemented: SendMessage, Task get/cancel, deployment-declared skills, per-principal authorization, waiting/input-required + `returnImmediately` async behavior, resuming sends, bounded streaming/resubscription (`SendStreamingMessage` / `SubscribeToTask`), security scheme advertisement, and interoperability evidence tests.**

## Reference Baseline

The project pins A2A maintenance release **1.0.1** and advertises wire protocol version **1.0** only for implemented/verified behavior.

For A2A wire semantics, use the official A2A Project website and generated definitions as the primary source of truth:

```text
https://a2a-protocol.org/latest/
https://a2a-protocol.org/latest/definitions/
```

The upstream release history is used to pin the reviewed maintenance release. External A2A v0.3 discovery/method/Part compatibility aliases are intentionally not retained.

## Open Workflow A2A Vocabulary Is a Separate Layer

The official Open Workflow 1.0.3 schema defines its own A2A call values such as `message/send`, `tasks/get`, and `tasks/cancel`.

OWA preserves that schema unchanged and translates those values at `RuntimeServices.call_protocol()` to the official A2A v1 wire operations:

```text
message/send  -> SendMessage
tasks/get     -> GetTask
tasks/cancel  -> CancelTask
```

This is a DSL-to-protocol adapter, not a legacy A2A wire compatibility layer.

## Current A2A Boundary

The runtime has a bounded common outbound A2A client and an optional inbound A2A server. Inbound A2A is disabled by default and deployment-controlled.

Implemented bindings:

```text
GET  /.well-known/agent-card.json

JSON-RPC at <configured path>
  SendMessage
  GetTask
  CancelTask
  SendStreamingMessage
  SubscribeToTask

HTTP+JSON
  POST <configured path>/message:send
  GET  <configured path>/tasks/{id}
  POST <configured path>/tasks/{id}:cancel
  POST <configured path>/message:stream
  POST <configured path>/tasks/{id}:subscribe
```

Selectable transports:

```text
jsonrpc    default
http_json
```

Current guarantees:

- A2A v1 Agent Card metadata with `supportedInterfaces`;
- bounded synchronous `SendMessage`;
- v1 Message/Part shapes;
- A2A Task projection over common invocation state;
- Task retrieval/cancellation using common `InvocationStore` and engine cancellation;
- deployment-configured public base URL;
- optional bearer authentication via named security profiles;
- per-principal authorization with action/resource/role policies;
- bounded request/message sizes;
- sanitized errors;
- no engine-native checkpoint/thread/run/stream exposure;
- identical common Task semantics independent of ADK/LangGraph;
- capability advertisement limited to implemented behavior;
- security scheme advertisement in Agent Card when auth is configured;
- bounded streaming with event/byte/duration limits and fail-closed backpressure.

Current non-guarantees:

- no push notifications;
- no broad/full A2A conformance claim;
- no delegated-user/token-exchange contract inside OWA;
- A2A streams are bounded SSE (events/bytes/duration) with re-subscription, not unlimited long-lived connections.

## Implemented A2A Task Projection

A2A Tasks are a view over common OWA invocation state, never a second workflow/persistence engine.

Identity:

```text
A2A task id   = OWA invocation_id
A2A contextId = OWA session_id
```

State mapping:

```text
OWA running    -> TASK_STATE_WORKING
OWA waiting    -> TASK_STATE_INPUT_REQUIRED
OWA completed  -> TASK_STATE_COMPLETED
OWA faulted    -> TASK_STATE_FAILED
OWA cancelled  -> TASK_STATE_CANCELED
```

Output projection follows the official v1 Part representation:

```text
string output      -> text Part
structured output  -> data Part
byte output        -> base64 raw Part
```

Waiting/failure status Messages carry `taskId` and `contextId`. Failure projection exposes only a sanitized common error code, never engine exceptions or secrets.

Official JSON-RPC Task error mappings implemented:

```text
Task not found       -32001
Task not cancelable  -32002
```

HTTP+JSON returns the corresponding bounded 404/400 error boundary.

## Existing Common Lifecycle SSE

The engine-neutral lifecycle stream remains:

```text
GET /v1/events/lifecycle/stream
```

It provides reusable bounded mechanics—SSE framing, replay, ordering, queues, backpressure, subscriber limits, event/byte/time limits, and sanitized lifecycle data—but it is **not** itself an A2A stream.

An A2A streaming endpoint must translate common lifecycle state into protocol-native Task/Message/Artifact stream responses rather than exposing raw OWA lifecycle CloudEvents as A2A payloads.

## Official Async Semantics

The official A2A v1 contract defines ordinary `SendMessage` as blocking by default. `SendMessageConfiguration.returnImmediately=true` is the protocol-native non-blocking option.

That gives OWA the required design rule:

```text
returnImmediately absent/false
  -> execute until terminal or interrupted Task state

returnImmediately true
  -> return the current Task projection without waiting for terminal completion
  -> client follows with GetTask and later SubscribeToTask when supported
```

OWA must not add a custom `async` flag.

This is now implemented. Blocking sends return `result.message` on completion or `result.task` when the workflow ends up waiting (`TASK_STATE_INPUT_REQUIRED`); `configuration.returnImmediately: true` starts the invocation and returns the Task projection immediately for `GetTask` polling.

## Waiting / Input Required / Resume

The common runtime already owns waiting and resume semantics. The A2A protocol projection is now exact:

```text
common waiting
  -> TASK_STATE_INPUT_REQUIRED
  -> TaskStatus.message explains that additional input is required

new client message / protocol-native continuation
  -> common invocation resume
  -> same task id/context id
  -> updated Task projection
```

Resuming sends carry `message.taskId` and reuse the common fingerprint-verified resume contract. Unknown tasks fail with `task_not_found`; non-waiting tasks are rejected with a sanitized `task is not accepting input` error.

## Shared Security Boundary

Framework-neutral security primitives now exist for:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

They include env-only secret references, secret-safe validation, principal/role/scope/audience modeling, and action/resource authorization rules.

Completed integration:

- profiles are part of the main strict runtime configuration (`RuntimeConfig.security.profiles`);
- A2A inbound authentication references a named `bearer` profile through `a2a.security_profile` (the temporary `auth_token` field is removed);
- per-principal authorization (`a2a.authorization`) enforces explicit allow rules;
- Agent Card advertises `securitySchemes` and `security` requirements when auth is configured;
- OAuth2 client-credentials and mTLS profiles are wired to outbound protocol adapters.

Delegated identity, OAuth/OIDC federation, token exchange, and consent remain external identity-platform concerns.

## A2A Streaming Implementation

Streaming is now implemented over the common lifecycle/event infrastructure:

```text
SendStreamingMessage (JSON-RPC) / message:stream (HTTP+JSON)
SubscribeToTask (JSON-RPC) / tasks/{id}:subscribe (HTTP+JSON)
```

Implementation details:

- translates common lifecycle CloudEvents into official A2A v1 `statusUpdate` and `artifactUpdate` frames;
- streams are bounded by event/byte/duration limits with fail-closed backpressure;
- disconnecting never cancels the underlying invocation;
- engine-native checkpoint/stream objects are never exposed;
- re-subscription via `SubscribeToTask` reconciles from the current task state;
- security and authorization are enforced identically to non-streaming operations.

## Push Notifications

Push notifications remain intentionally deferred because they add an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, callback authentication, SSRF protection, replay/idempotency controls, bounded retries/dead-letter behavior, and secret-safe observability.

## Current Capability Position

```text
common lifecycle SSE                    implemented
inbound A2A Agent Card                  implemented
A2A SendMessage                         implemented
A2A Task projection                     implemented
A2A GetTask                             implemented
A2A CancelTask                          implemented
jsonrpc transport                       implemented
http_json transport                     implemented
shared security primitives              implemented
multi-skill routing                     implemented
waiting/resume A2A mapping              implemented
returnImmediately async                 implemented
A2A streaming/resubscription            implemented
security scheme advertisement           implemented
interoperability evidence tests         implemented
push notifications                      intentionally deferred
full A2A conformance claim              intentionally deferred
```

The bounded inbound A2A profile is complete. No active implementation work remains in this bounded profile; broader interoperability/full-conformance work and the intentionally deferred items (push notifications and a full conformance claim) remain outside scope.
