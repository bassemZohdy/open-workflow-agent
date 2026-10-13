# External Protocol Baselines

Verified: 2026-08-29

Open Workflow Agent targets reviewed stable releases of external protocols, with an explicit exception for its existing Open Workflow 1.0.3 public DSL. Upstream marks that release as a prerelease. The canonical machine-readable project record is `resources/protocol-baselines.yaml`. Baselines are pinned per reviewed project state and never float automatically at runtime.

## Current Baselines

| Protocol / specification | Pinned reviewed baseline | OWA status |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | Prerelease DSL; pinned upstream schema snapshot / OWA Portable Profile |
| A2A Protocol | release `1.0.1`, protocol `1.0` | Bounded v1 SendMessage + Task get/cancel profile |
| Model Context Protocol | `2026-07-28` | Bounded common client/tool profile |
| OpenAPI Specification | `3.2.0` | Bounded operation adapter only; no full parser/conformance claim |
| CloudEvents | `1.0.2` | Bounded lifecycle snapshot/SSE profile using `specversion: 1.0` |
| AsyncAPI Specification | `3.1.0` | Future binding baseline; not implemented |
| gRPC Python SDK | `1.84.0` | Authored deployment-bound unary adapter with Protobuf `7.36.1`; qualification pending |

## CI Drift Guard

`tests/core/test_protocol_baselines.py` ties the manifest to:

- runtime A2A and MCP protocol constants;
- the bundled Open Workflow `1.0.3` schema;
- the bounded supported A2A/MCP operation sets;
- documentation baseline rows;
- the project rule that no broad conformance claim is made by default.

Because this test is part of the root quality gate used before release, changing a runtime-advertised baseline without updating/reviewing the canonical manifest and documentation fails CI.

## Open Workflow Specification — 1.0.3

Reviewed on 2026-10-11: [upstream v1.0.3](https://github.com/open-workflow-specification/specification/releases/tag/v1.0.3)
is marked prerelease at tag commit `9b5b1da29e9d4fff2358580241e11aab22704a16`.
OWA retains its existing public DSL and the unmodified schema from commit
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a`, SHA-256
`704ef5e91c5d823167dd8751794edb1dd1a6f9a3bdf9bfd389bf9c6b23ae3816`.
The schema snapshot includes upstream changes after the release tag; it is
not the tag's schema and must not be presented as a stable release schema.
See [schema provenance](../resources/open-workflow/1.0.3/SCHEMA_SOURCE.md)
and the [definition admission profile](workflow-admission.md). Runtime loading
checks the digest, including a deployment-provided `OWA_SCHEMA_PATH`.

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

S3-02 authors the reviewed official Python `a2a-sdk==1.2.2`, tag commit
`2f9e44af243df5c7c7a3da361c8d095324bcd5e3` on 2026-10-13. Five independent
locks pin its artifacts. Enabled inbound startup, route mounting and card
building reject missing or different SDK versions; sanitized capabilities
separate configured `enabled` from `effectiveEnabled` and SDK availability.
A2A wire protocol remains `1.0` and no full conformance is claimed. This
implementation profile remains unqualified until the
[finite S3-02 gates](s3-02-a2a-acceptance.md) pass. Initial asynchronous Task
acknowledgment follows durable admission; existing-task asynchronous or
streaming input resume remains explicitly unsupported pending durable
wait-revision consumption. Blocking protected resume is retained.

### Outbound A2A client method set

The common outbound A2A client (`ProtocolServices.call(protocol="a2a")`) accepts exactly these official v1 JSON-RPC wire operations:

```text
SendMessage
GetTask
ListTasks
CancelTask
GetExtendedAgentCard
SendStreamingMessage
SubscribeToTask
```

Any other wire method, including push-notification configuration operations, is rejected before any network call. The unchanged official Open Workflow 1.0.3 inline A2A schema includes `tasks/list` and `agent/getAuthenticatedExtendedCard` alongside send, stream, get, cancel and resubscribe. The optional `parameters` property cannot be an empty object; no-argument requests omit it. Configured agent tools use the same common SDK service. Internal normalization does not expand the official schema or enable separately gated push operations.

Legacy v0.3 discovery paths/method names remain unsupported on the A2A wire endpoint.

The inbound A2A boundary additionally implements streaming/resubscription (A2A-7): `SendStreamingMessage` (HTTP+JSON `message:stream`) starts a task and streams official `statusUpdate`/`artifactUpdate` frames until terminal state, and `SubscribeToTask` (HTTP+JSON `tasks/{id}:subscribe`) attaches to an existing task. Streams translate common lifecycle events, are bounded (events/bytes/duration, then the client re-subscribes), and never expose engine-native checkpoint or stream objects. The S3-02 authored outbound stream path validates bounded correlated v1 alternatives, task/context identity and terminal state without reconnect or replay. Its integrated qualification remains pending.

Not yet advertised:

- push notifications;
- broad/full A2A conformance.

The official A2A specification defines ordinary `SendMessage` as blocking by default and uses `returnImmediately=true` for the protocol-native non-blocking path. OWA follows that contract rather than introducing a custom async flag.

## Model Context Protocol — 2026-07-28

The common OWA MCP client is pinned to `2026-07-28` and covered by deterministic method/version/transport tests. OWA claims only the bounded operations implemented by `ProtocolServices`, not complete MCP conformance.

S3-01 authors the pinned official Python SDK `mcp==2.3.0` and matching
`mcp-types==2.3.0`, reviewed at tag commit
`2118f14f8a19bc158d8a1cf90af58d85d187f849` on 2026-10-13. Installed versions
outside that pin fail before transport or credential I/O. All independent
project locks carry the dependency; invocation never installs packages.
See the [finite acceptance gates](s3-01-mcp-acceptance.md), which remain pending
until the integrated source, platform, database and hosted batch is qualified.

The authored common client handles seven typed methods and returns the
validated JSON-RPC result, preserving explicit nulls. HTTP supports JSON and
a bounded single correlated SSE response, with no reconnect or hidden replay.
Stdio requires the approved internal sandbox binding and remains disabled by
default. Unsupported interactions, legacy negotiation, interactive OAuth and
delegation are explicit limits. Bound client credentials and static HTTP
identities do not claim the full MCP discovery/authorization protocol.

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

AsyncAPI `3.1.0` is pinned only as a future binding baseline. OWA does not
currently implement or advertise AsyncAPI behavior. The transport-neutral
preparation, event security/delivery rules, and activation criteria are
maintained in the [event integration readiness plan](event-integration-readiness.md).

Authoritative source:

```text
https://www.asyncapi.com/docs/reference/specification/v3.1.0
```

## Upgrade Policy

The selected gRPC adapter pins the reviewed [gRPC1.84.0 release](https://github.com/grpc/grpc/releases/tag/v1.84.0),
commit `3252a89f10d8e92997862167ca7d095ecda85973`, and Python Protobuf `7.36.1`
from the independent locked dependency profiles. SDK release numbering is not a
new public workflow DSL or a wire-version negotiation. The authored profile uses
deployment-bound closed compiled descriptors and unary calls, with no runtime
compilation/reflection or streaming activation. Default capability remains
disabled unless a deployment supplies the complete reviewed binding. Authoring
and these pins do not prove behavioral/native/backend/platform qualification;
the finite acceptance record and independent gates remain required.

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
