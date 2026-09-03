# A2A and Streaming Evaluation

Status: **bounded inbound A2A v1 profile implemented: SendMessage, Task get/cancel, deployment-declared skills, per-principal authorization, waiting/input-required + `returnImmediately` async behavior, resuming sends, and bounded streaming/resubscription (`SendStreamingMessage` / `SubscribeToTask`).**

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

HTTP+JSON
  POST <configured path>/message:send
  GET  <configured path>/tasks/{id}
  POST <configured path>/tasks/{id}:cancel
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
- optional temporary deployment bearer guard;
- bounded request/message sizes;
- sanitized errors;
- no engine-native checkpoint/thread/run/stream exposure;
- identical common Task semantics independent of ADK/LangGraph;
- capability advertisement limited to implemented behavior.

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

Before implementing this, the runtime needs a clean common way to launch/retain an active invocation while returning its persistent `ExecutionHandle` projection without binding the public contract to an in-process engine task object.

## Waiting / Input Required / Resume

The common runtime already owns waiting and resume semantics. The remaining A2A work is to make the protocol projection exact:

```text
common waiting
  -> TASK_STATE_INPUT_REQUIRED
  -> TaskStatus.message explains that additional input is required

new client message / protocol-native continuation
  -> common invocation resume
  -> same task id/context id
  -> updated Task projection
```

`AUTH_REQUIRED` must not be invented from ordinary waiting state. It should be introduced only when an actual authentication continuation requirement exists.

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
- A2A inbound authentication references a named `bearer` profile through `a2a.security_profile` (the temporary `auth_token` field is removed).

Remaining integration before expanding A2A:

- advertise official Agent Card `securitySchemes` / `securityRequirements` accurately;
- authenticate at HTTP/TLS layer, never in A2A message payloads;
- authorize protocol-native actions such as `message.send`, `tasks.get`, and `tasks.cancel` against the selected skill/resource;
- map clients only to deployment-declared skills/workflows.

Delegated identity, OAuth/OIDC federation, token exchange, and consent remain external identity-platform concerns.

## Remaining Implementation Order

```text
1. RuntimeConfig + protocol integration for named security profiles
2. deployment-declared A2A skills -> registered workflows
3. per-principal skill/action authorization
4. waiting/input-required/resume protocol mapping
5. SendMessageConfiguration.returnImmediately
6. SendStreamingMessage over common lifecycle/event infrastructure
7. SubscribeToTask reconciliation/resubscription
8. external interoperability/conformance evidence
```

The earlier Task-model blocker is removed. The current blockers are authorization/skill routing and the precise portable async/resume contract.

## Streaming Rules

When A2A streaming is implemented:

- use official `SendStreamingMessage` / `SubscribeToTask` semantics;
- stream incrementally rather than buffering the response;
- project common Task/Message/Artifact state, not engine-native streams;
- preserve the same security/authorization as non-streaming operations;
- preserve bounded queues, backpressure, time/byte/event limits, and sanitized errors;
- use protocol-native resubscription/reconciliation;
- advertise streaming only after deterministic and interoperability gates are green.

## Push Notifications

Push notifications remain intentionally deferred because they add an outbound callback trust boundary requiring callback allowlisting, TLS/server identity verification, callback authentication, SSRF protection, replay/idempotency controls, bounded retries/dead-letter behavior, and secret-safe observability.

## Current Capability Position

```text
common lifecycle SSE          implemented
inbound A2A Agent Card        implemented
A2A SendMessage               implemented
A2A Task projection           implemented
A2A GetTask                   implemented
A2A CancelTask                implemented
jsonrpc transport             implemented
http_json transport           implemented
shared security primitives    implemented; adapter integration active
multi-skill routing           active backlog
waiting/resume A2A mapping    active backlog
returnImmediately async       active backlog
A2A streaming/resubscription  active backlog
push notifications            intentionally deferred
full A2A conformance claim    intentionally deferred
```

The architectural conclusion is now:

> The A2A Task projection/get/cancel blocker is removed. Complete shared authorization, deployment-owned skill routing, and protocol-native async/resume semantics before advertising streaming.
