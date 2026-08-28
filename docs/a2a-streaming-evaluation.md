Status: B-007 implementation in progress. The first bounded slice is **common runtime lifecycle streaming over SSE**. Inbound A2A exposure, A2A task streaming, gRPC streaming, and push notifications remain disabled and are not advertised.

## Reference baseline

The implementation review is aligned to the released A2A **1.0.x** line. The latest maintenance release verified for this work is **1.0.1** (released 2026-05-26); 1.0.0 was released 2026-03-12.

- https://a2a-protocol.org/latest/specification/
- https://github.com/a2aproject/A2A

A2A 1.0 treats agents as standard network applications, declares security requirements through the Agent Card, supports multiple protocol bindings, and requires equivalent behavior when multiple bindings are exposed. Streaming is transport-specific; HTTP-based bindings use Server-Sent Events while gRPC uses server streaming.

## Current Open Workflow Agent boundary

The runtime has a bounded **outbound** A2A JSON-RPC client path in `ProtocolServices`. It is a workflow/tool protocol client, not an A2A server implementation.

Current guarantees:

- outbound calls use the common bounded HTTP client;
- endpoint allowlisting, TLS verification, response-size limits, request timeouts, and deployment-owned environment authentication remain in the common protocol layer;
- A2A payloads do not receive engine-native state or checkpoint objects;
- ADK and LangGraph use the same common protocol service.

Current non-guarantees:

- no inbound A2A endpoint or Agent Card is exposed;
- no A2A conformance claim is made;
- no user-delegation or agent-to-agent token-exchange contract is defined by this runtime;
- no durable task subscription, push-notification receiver, or webhook trust boundary exists;
- engine-native/token streaming remains disabled;
- the current outbound HTTP protocol client buffers bounded responses, so allowing a streaming A2A method name is **not** equivalent to implementing A2A streaming semantics.

## B-007 first implementation slice: portable lifecycle SSE

The selected first slice is intentionally below the A2A server boundary. It exposes common sanitized lifecycle CloudEvents through:

`GET /v1/events/lifecycle/stream`

The stream is engine-neutral and built from the existing common lifecycle event sink. It does not expose ADK or LangGraph native checkpoints, execution objects, or stream primitives.

Implemented contract:

- Server-Sent Events transport;
- CloudEvents 1.0 lifecycle payloads;
- per-runtime emission ordering;
- synchronous subscriber registration before response control is yielded, avoiding publish-to-subscribe races;
- bounded replay using `Last-Event-ID` while the cursor remains in the retained lifecycle snapshot;
- fail-closed `409 stream_replay_unavailable` when a replay cursor has expired;
- bounded subscriber queues with slow-consumer termination rather than unbounded buffering;
- bounded event count, byte count, stream lifetime, and queue size;
- explicit `stream.end` terminal reason (`event_limit`, `byte_limit`, `timeout`, or `backpressure`);
- disconnecting an observation stream does **not** cancel the workflow invocation;
- a maximum active-subscriber boundary in the common lifecycle sink;
- exact runtime capability metadata under `features.lifecycleStreaming`;
- `features.streaming` remains the engine-native streaming flag and therefore remains `false` for ADK and LangGraph.

Default per-connection limits are deliberately conservative:

- 100 events;
- 1 MiB encoded stream bytes;
- 30 seconds;
- 64 queued live events;
- 32 active lifecycle subscribers per runtime sink.

HTTP query parameters may tighten or expand a connection only inside hard ceilings enforced by the API (`max_events <= 1000`, `max_bytes <= 16 MiB`, `timeout_seconds <= 300`, `queue_size <= 1000`).

## Required boundary before inbound A2A

Inbound A2A remains optional and deployment-controlled. Before implementation, define:

1. **Discovery and identity**
   - explicit Agent Card ownership and endpoint;
   - deployment-defined skills and capabilities;
   - no engine implementation details in the card.

2. **Authentication and authorization**
   - TLS for production bindings;
   - supported `securitySchemes` declared in the Agent Card;
   - deployment authentication at the transport/protocol boundary;
   - authorization per skill/action and least-privilege scopes;
   - user delegation, if supported later, represented by the deployment identity layer rather than hidden inside A2A message data.

3. **Request safety**
   - request/body limits;
   - content/file limits;
   - concurrency and rate limits;
   - validated task/message/artifact input;
   - bounded execution and cancellation propagation.

4. **Observability**
   - common invocation/task correlation IDs;
   - sanitized lifecycle/error mapping;
   - no bearer tokens, secrets, engine checkpoints, container IDs, pod names, or framework-native state in public events.

## A2A streaming decision

Do **not** advertise A2A streaming until an inbound A2A server contract exists and maps its task/message/artifact semantics onto the now-implemented common lifecycle stream without weakening authentication or authorization boundaries.

The lifecycle SSE slice establishes reusable streaming invariants, but it is not an A2A protocol binding. A future HTTP A2A binding must stream incrementally rather than buffer the full response. A future gRPC binding must map server-stream semantics onto an equivalent common event model.

## Push notifications

Defer push notifications until there is a dedicated outbound-webhook security policy covering:

- callback endpoint allowlisting;
- TLS/server identity verification;
- authentication of notifications;
- SSRF protection;
- replay/idempotency protection;
- retry limits and dead-letter behavior;
- secret-safe logging.

## Capability position

With the first B-007 slice implemented:

- common lifecycle SSE: **implemented on this branch; pending CI and merge**;
- inbound A2A: **disabled / not advertised**;
- A2A streaming: **disabled / not advertised**;
- A2A push notifications: **disabled / not advertised**;
- bounded outbound A2A calls: common protocol-client feature only;
- ADK/LangGraph native streaming: **not advertised**.

This keeps the public boundary honest: a useful portable streaming primitive is implemented without turning outbound A2A method support into a false full-server or conformance claim.