# External Protocol Baselines

Verified: 2026-08-29

Open Workflow Agent targets reviewed stable releases of the external protocols/specifications it implements or advertises. The canonical machine-readable project record is `resources/protocol-baselines.yaml`. Baselines are pinned per reviewed project state and never float automatically at runtime.

## Current Baselines

| Protocol / specification | Pinned stable baseline | OWA status |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | Implemented subset / OWA Portable Profile |
| A2A Protocol | release `1.0.1`, protocol `1.0` | Bounded v1 SendMessage + Task get/cancel profile |
| Model Context Protocol | `2026-07-28` | Bounded common client/tool profile |
| OpenAPI Specification | `3.2.0` | Bounded operation adapter only; no full parser/conformance claim |
| CloudEvents | `1.0.2` | Bounded lifecycle snapshot/SSE profile using `specversion: 1.0` |
| AsyncAPI Specification | `3.1.0` | Future binding baseline; not implemented |

## CI Drift Guard

`tests/core/test_protocol_baselines.py` ties the manifest to:

- runtime A2A and MCP protocol constants;
- the bundled Open Workflow `1.0.3` schema;
- the bounded supported A2A/MCP operation sets;
- documentation baseline rows;
- the project rule that no broad conformance claim is made by default.

Because this test is part of the root quality gate used before release, changing a runtime-advertised baseline without updating/reviewing the canonical manifest and documentation fails CI.

## Open Workflow Specification — 1.0.3

OWA bundles and validates against the official Open Workflow schema revision:

```text
https://open-workflow-specification.org/schemas/1.0.3/workflow.yaml
```

The schema is not modified by OWA. OWA advertises its tested Portable Profile rather than claiming full Open Workflow conformance.

The Open Workflow A2A task schema defines call values such as:

```text
message/send
tasks/get
tasks/cancel
```

These values are the Open Workflow DSL vocabulary. They are not external A2A wire compatibility aliases. OWA translates them at `RuntimeServices.call_protocol()` to the pinned A2A v1 wire operation names while leaving the Open Workflow schema untouched.

Authoritative project source:

```text
https://github.com/open-workflow-specification/specification
```

## A2A Protocol — release 1.0.1 / protocol 1.0

For A2A wire semantics, use the **official A2A Project website and generated protocol definitions** as the primary source of truth:

```text
https://a2a-protocol.org/latest/
https://a2a-protocol.org/latest/definitions/
```

The upstream release history is used to pin the reviewed maintenance release (`1.0.1`), while the Agent Card advertises the A2A wire protocol version `1.0`.

Implemented bounded v1 boundary:

```text
GET  /.well-known/agent-card.json

JSON-RPC:
  SendMessage
  GetTask
  CancelTask

HTTP+JSON:
  POST /message:send
  GET  /tasks/{id}
  POST /tasks/{id}:cancel
```

Implemented v1 semantics also include:

- `supportedInterfaces` with protocol version `1.0`;
- v1 Message/Part shapes without v0.3 `kind` discriminators;
- Task projection over common OWA invocation state;
- Task state mapping to `TASK_STATE_WORKING`, `INPUT_REQUIRED`, `COMPLETED`, `FAILED`, and `CANCELED`;
- Task artifacts using text/data/raw Part shapes; raw bytes use base64 JSON encoding;
- JSON-RPC Task-not-found `-32001`;
- JSON-RPC Task-not-cancelable `-32002`;
- capability advertisement limited to implemented Task operations.

### Outbound A2A client method set

The common outbound A2A client (`ProtocolServices.call(protocol="a2a")`) accepts exactly these official v1 JSON-RPC wire operations:

```text
SendMessage
GetTask
ListTasks
CancelTask
GetExtendedAgentCard
```

Any other wire method — including `SendStreamingMessage`, `SubscribeToTask`, and the push-notification config operations — is rejected before any network call. Open Workflow DSL call values such as `tasks/list` and `agent/getAuthenticatedExtendedCard` are translated at the `RuntimeServices.call_protocol()` boundary onto the wire methods above; DSL values whose translated wire operation is outside this set fail closed.

Legacy v0.3 discovery paths/method names remain unsupported on the A2A wire endpoint.

The inbound A2A boundary additionally implements streaming/resubscription (A2A-7): `SendStreamingMessage` (HTTP+JSON `message:stream`) starts a task and streams official `statusUpdate`/`artifactUpdate` frames until terminal state, and `SubscribeToTask` (HTTP+JSON `tasks/{id}:subscribe`) attaches to an existing task. Streams translate common lifecycle events, are bounded (events/bytes/duration, then the client re-subscribes), and never expose engine-native checkpoint or stream objects. The outbound client above remains pinned to the non-streaming operation set; outbound streaming follows when a concrete requirement exists.

Not yet advertised:

- push notifications;
- broad/full A2A conformance.

The official A2A specification defines ordinary `SendMessage` as blocking by default and uses `returnImmediately=true` for the protocol-native non-blocking path. OWA follows that contract rather than introducing a custom async flag.

## Model Context Protocol — 2026-07-28

The common OWA MCP client is pinned to `2026-07-28` and covered by deterministic method/version/transport tests. OWA claims only the bounded operations implemented by `ProtocolServices`, not complete MCP conformance.

Authoritative sources:

```text
https://modelcontextprotocol.io/
https://blog.modelcontextprotocol.io/posts/2026-07-28/
```

## OpenAPI Specification — 3.2.0

OWA exposes a bounded operation adapter. It does not claim a complete OAS 3.2 parser or conformance implementation.

Authoritative source:

```text
https://spec.openapis.org/oas/latest.html
```

## CloudEvents — 1.0.2

OWA exposes bounded lifecycle CloudEvents JSON snapshots and SSE observations using CloudEvents `specversion: 1.0`. This is a bounded runtime profile, not a claim of implementing every CloudEvents transport/binding.

Authoritative source:

```text
https://github.com/cloudevents/spec
```

## AsyncAPI Specification — 3.1.0

AsyncAPI `3.1.0` is pinned only as a future binding baseline. OWA does not currently implement or advertise AsyncAPI behavior.

Authoritative source:

```text
https://www.asyncapi.com/docs/reference/specification/v3.1.0
```

## Upgrade Policy

A new upstream stable release triggers review, not an automatic runtime change:

```text
new stable release
     ↓
compatibility/security review
     ↓
implementation/migration
     ↓
deterministic tests
     ↓
capability metadata update
     ↓
OWA release
```

Draft, preview, RC, and editor-draft specifications must not become the default production baseline.

Because the OWA public contract is still stabilizing, external protocol compatibility layers are not retained automatically. Compatibility is explicit and reviewed.

## Advertisement Rule

`/v1/capabilities`, Agent Cards, protocol metadata, and release documentation may advertise only behavior that is both implemented and covered by applicable deterministic/acceptance gates.

A pinned baseline is a review target, not by itself a conformance claim.
