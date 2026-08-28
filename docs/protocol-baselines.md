# External Protocol Baselines

Verified: 2026-08-29

Open Workflow Agent targets the latest stable released version of each external protocol/specification it implements or advertises. Baselines are pinned per project release or reviewed `main` state; they never float automatically at runtime.

## Current Baselines

| Protocol / specification | Pinned stable baseline | OWA status |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | Implemented subset / OWA Portable Profile |
| A2A Protocol | `1.0.1` | Bounded inbound v1 profile implemented; Task/streaming/conformance expansion remains backlog |
| Model Context Protocol | `2026-07-28` | Common client migration implemented on `main`; final compatibility/advertisement audit remains active |
| OpenAPI Specification | `3.2.0` | Bounded operation adapter only; no full parser/conformance claim |
| CloudEvents | `1.0.2` | Bounded lifecycle snapshot/SSE behavior implemented; exact compatibility verification remains active |
| AsyncAPI Specification | `3.1.0` | Future binding baseline |
| gRPC | current stable protocol/toolchain | No independent OWA application-protocol version is advertised unless a concrete binding is implemented |

## Authoritative Verification

### Open Workflow Specification — 1.0.3

The official workflow schema used by OWA identifies itself as:

```text
https://open-workflow-specification.org/schemas/1.0.3/workflow.yaml
```

OWA bundles and validates against this exact schema revision.

Authoritative source:

```text
https://github.com/open-workflow-specification/specification
```

OWA intentionally advertises only its tested Portable Profile rather than claiming full Open Workflow conformance.

### A2A Protocol — 1.0.1

The official A2A project lists `v1.0.1` as the stable maintenance release used by this project. OWA has migrated the bounded inbound profile away from legacy `0.3.0` assumptions.

Implemented v1 boundary includes:

```text
GET  /.well-known/agent-card.json
JSON-RPC SendMessage
HTTP+JSON /message:send
supportedInterfaces
protocolVersion: 1.0
v1 message/Part shapes
```

Legacy v0.3 discovery paths, method names, and Part compatibility aliases are intentionally not retained.

Persistent A2A Tasks, task retrieval/cancellation, protocol-native async Task behavior, streaming/resubscription, push notifications, and broad conformance remain outside the currently advertised bounded profile.

Authoritative sources:

```text
https://github.com/a2aproject/A2A/releases
https://a2a-protocol.org/latest/
```

### Model Context Protocol — 2026-07-28

The MCP project released specification revision `2026-07-28`, including the stateless protocol core and the current authorization/task semantics.

The common OWA MCP client migration is implemented on `main`; final protocol-wide audit and compatibility/advertisement gates remain active work before making a broader support claim.

Authoritative sources:

```text
https://modelcontextprotocol.io/
https://blog.modelcontextprotocol.io/posts/2026-07-28/
```

### OpenAPI Specification — 3.2.0

The OpenAPI Initiative identifies `3.2.0` as the current stable OpenAPI Specification baseline used for OWA review.

OWA currently exposes a bounded operation adapter. It does not claim a complete OAS 3.2 parser or conformance implementation.

Authoritative source:

```text
https://spec.openapis.org/oas/latest.html
```

### CloudEvents — 1.0.2

The CloudEvents specification repository identifies `ce@v1.0.2` as the stable core/event-format family baseline used by OWA.

OWA exposes bounded lifecycle CloudEvents snapshots and lifecycle SSE. Exact compatibility verification remains part of the active protocol gate before broadening any conformance wording.

Authoritative source:

```text
https://github.com/cloudevents/spec
```

### AsyncAPI Specification — 3.1.0

AsyncAPI `3.1.0` is the target baseline if/when an AsyncAPI binding is implemented.

Authoritative source:

```text
https://www.asyncapi.com/docs/reference/specification/v3.1.0
```

### gRPC

gRPC does not define an OWA-level application schema version analogous to A2A, MCP, OpenAPI, or AsyncAPI. If OWA introduces a gRPC binding, the project must pin the relevant protobuf definitions and stable library/toolchain versions and advertise only the application contract actually implemented.

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

Because the OWA public contract is still stabilizing, legacy protocol generations are removed during migration rather than maintained automatically as compatibility layers.

## Advertisement Rule

`/v1/capabilities`, Agent Cards, protocol metadata, and release documentation may advertise only behavior that is both implemented and covered by the applicable deterministic/acceptance gates.

A pinned baseline is therefore a review target, not by itself a conformance claim.
