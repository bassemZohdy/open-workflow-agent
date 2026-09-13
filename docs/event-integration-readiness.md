# Event Integration Readiness Plan

Status: optional transport-neutral readiness plan. OWA does not currently
implement or advertise a durable broker binding or an AsyncAPI document, and
neither is required for the current open-source core.

## Current event boundary

OWA currently provides:

- a normalized generic event endpoint whose delivery is process-local and
  non-durable;
- bounded lifecycle CloudEvents snapshots;
- bounded lifecycle SSE with replay limits and fail-closed backpressure;
- common identifiers and sanitized payloads without engine-native state.

This is an observation and lifecycle profile, not a general-purpose event
broker, queue, or durable integration contract.

## Best practices that apply now

The following rules are safe regardless of the eventual transport:

- Use a CloudEvents-style envelope with stable `id`, `source`, `type`,
  `specversion`, `time`, and explicit data content type.
- Version event types and payload schemas deliberately; do not silently change
  the meaning of an existing event type.
- Correlate events with `invocation_id`, `session_id`, workflow name/version,
  Open Workflow task reference, and a causation/correlation relationship where
  applicable. Never expose engine-native checkpoint or run identifiers.
- Treat delivery as at-least-once unless a selected transport and durable
  protocol prove otherwise. Consumers must be idempotent.
- Bound event size, queue depth, replay, connection lifetime, and retry work.
  Apply backpressure and fail closed when limits are exceeded.
- Redact secrets and sensitive workflow input from event data, logs, dead-letter
  records, and replay storage.
- Require deployment-owned authentication, TLS, authorization, and endpoint
  policy for external delivery. Event payloads must not carry credentials.
- Make ordering, retention, replay, retry, dead-letter, and loss behavior
  explicit instead of implying guarantees from an in-process queue.

## Requirements before a concrete integration

An event integration requirement must identify:

1. the consumer and business use case;
2. the chosen transport or broker (for example HTTP, Kafka, AMQP, WebSockets,
   or SSE);
3. delivery semantics, ordering scope, retention, replay, and failure policy;
4. authentication, authorization, TLS, network, and secret-management policy;
5. payload schemas, compatibility/versioning policy, and maximum sizes;
6. operational ownership for capacity, retries, dead letters, metrics, and
   incident response; and
7. whether tenant boundaries or delegated identity affect routing and access.

Best practices provide safe defaults for these decisions, but they cannot
choose the transport, durability, or ownership model without a real consumer
and deployment context.

The optional future work is tracked as `EVENT-1` through `EVENT-5` in
`TODO.md`. `EVENT-1` is the activation gate for that future track; the
remaining tasks must not be started as runtime implementation until a real
consumer and requirement are recorded. No event-integration decision is needed
to use or host the current core.

## AsyncAPI adoption sequence

When the requirements above exist:

1. Define the implemented event types and JSON Schemas first.
2. Select the actual server, channel, operation, and protocol binding.
3. Author an AsyncAPI 3.1 document that describes only implemented behavior.
4. Validate the document in CI and test producer/consumer interoperability.
5. Add capability and deployment documentation only after the integration and
   security gates are green.

AsyncAPI is protocol-agnostic and describes message-driven APIs; it is not a
replacement for selecting the event transport or implementing delivery. See
the [AsyncAPI 3.1 specification](https://www.asyncapi.com/docs/reference/specification/v3.1.0).

## Non-goals until activation

- Do not add a broker dependency merely to produce an AsyncAPI file.
- Do not claim durable delivery, exactly-once processing, or cross-process
  replay for the current process-local event endpoint.
- Do not expose generic eventing as a second approval, invocation, or A2A
  state-management system.
- Do not advertise an AsyncAPI binding before its transport and integration
  tests exist.

The current CloudEvents foundation remains the portable common layer. A future
transport adapter must preserve that layer and the public Open Workflow
contract, but the current process-local event endpoint remains an adequate
bounded baseline until a concrete integration need appears.
