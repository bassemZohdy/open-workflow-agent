# A2A Exposure and Streaming Evaluation

Status: B-007 evaluation baseline. This document does **not** enable inbound A2A exposure or advertise streaming as a portable runtime capability.

## Reference baseline

This evaluation targets the latest released A2A specification, **1.0.0**:

- https://a2a-protocol.org/v1.0.0/specification/
- https://github.com/a2aproject/A2A

A2A 1.0 treats agents as standard network applications, declares security requirements through the Agent Card, supports multiple protocol bindings, and requires equivalent behavior when multiple bindings are exposed. Streaming is transport-specific; HTTP-based bindings use Server-Sent Events while gRPC uses server streaming.

## Current Open Workflow Agent boundary

The runtime currently has a bounded **outbound** A2A JSON-RPC client path in `ProtocolServices`. It is a workflow/tool protocol client, not an A2A server implementation.

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
- no common streaming abstraction exists across engines;
- the current HTTP client buffers bounded responses, so allowing a streaming A2A method name is **not** equivalent to implementing A2A streaming semantics.

## Required boundary before inbound A2A

Inbound A2A must remain optional and deployment-controlled. Before implementation, define:

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

## Streaming decision

Do **not** advertise `streaming: true` until a common runtime streaming contract exists.

The common contract must define:

- event ordering guarantees;
- bounded buffering/backpressure;
- connection cancellation and invocation cancellation mapping;
- disconnect behavior;
- reconnection/resubscription semantics;
- stream completion and terminal error signaling;
- common ADK/LangGraph observable behavior;
- per-stream byte/time/concurrency limits.

For HTTP A2A, the implementation must support SSE incrementally rather than buffering the full response. For gRPC, server-stream semantics must map to the same common event model if that binding is added.

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

Until the above boundaries are implemented and cross-engine acceptance is green:

- inbound A2A: **disabled / not advertised**;
- A2A streaming: **disabled / not advertised**;
- A2A push notifications: **disabled / not advertised**;
- bounded outbound A2A calls: remain a common protocol-client feature only.

This keeps B-007 optional and prevents outbound method support from being mistaken for full A2A server or streaming conformance.
