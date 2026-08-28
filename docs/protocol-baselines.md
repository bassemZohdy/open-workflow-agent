# External Protocol Baselines

Verified: 2026-08-28

Open Workflow Agent targets the latest stable released version of each external protocol/specification it implements or advertises. The baseline is pinned per project release; it does not float automatically at runtime.

## Current baselines

| Protocol / specification | Pinned stable baseline | OWA status |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | Implemented subset / OWA Portable Profile |
| A2A Protocol | `1.0.1` | Migration required from legacy `0.3.0` assumptions before further expansion |
| Model Context Protocol | `2026-07-28` | Existing MCP client/tool behavior requires audit against this baseline |
| OpenAPI Specification | `3.2.0` | Planned/implemented bounded adapter behavior must validate against this baseline |
| CloudEvents | `1.0.2` | Existing lifecycle event boundary requires compatibility verification |
| AsyncAPI Specification | `3.1.0` | Future binding baseline |
| gRPC | current stable protocol/toolchain | No independent OWA application-protocol version is currently advertised |

## Authoritative verification

### Open Workflow Specification — 1.0.3

The official specification repository's current workflow schema identifies itself as:

```text
https://open-workflow-specification.org/schemas/1.0.3/workflow.yaml
```

OWA already bundles and validates against this exact schema revision. No migration is required by PROTOCOL-1.

Authoritative source:

```text
https://github.com/open-workflow-specification/specification
```

### A2A Protocol — 1.0.1

The official A2A specification repository lists `v1.0.1` as the latest protocol specification release (2026-05-26). OWA's current bounded inbound profile still contains legacy `0.3.0` metadata/discovery assumptions and therefore requires migration.

Authoritative sources:

```text
https://github.com/a2aproject/A2A/releases
https://a2a-protocol.org/latest/
```

OWA will not retain A2A v0.3 compatibility after migration unless a future explicit product decision requires it.

### Model Context Protocol — 2026-07-28

The MCP project released specification revision `2026-07-28`, including the stateless protocol core, multi-round-trip requests, header-based routing, authorization hardening, extensions, and updated task semantics.

Authoritative source:

```text
https://modelcontextprotocol.io/
https://blog.modelcontextprotocol.io/posts/2026-07-28/
```

Existing OWA MCP behavior must be audited before the runtime claims this baseline.

### OpenAPI Specification — 3.2.0

The OpenAPI Initiative identifies `3.2.0` (2025-09-19) as the latest stable OpenAPI Specification.

Authoritative source:

```text
https://spec.openapis.org/oas/latest.html
```

### CloudEvents — 1.0.2

The CloudEvents specification repository identifies `ce@v1.0.2` as the latest stable core specification and stable protocol/event-format family release.

Authoritative source:

```text
https://github.com/cloudevents/spec
```

OWA currently emits CloudEvents-compatible lifecycle events; PROTOCOL-3 must verify exact compatibility before broadening any conformance wording.

### AsyncAPI Specification — 3.1.0

The AsyncAPI Initiative released specification `3.1.0` on 2026-01-31. It is the target baseline when AsyncAPI support is implemented.

Authoritative source:

```text
https://www.asyncapi.com/docs/reference/specification/v3.1.0
```

### gRPC

gRPC does not define an OWA-level application schema version analogous to A2A, MCP, OpenAPI, or AsyncAPI. When OWA introduces a gRPC binding, the project must pin the relevant protobuf definitions and stable library/toolchain versions used by that binding and advertise only the application protocol version actually implemented.

## Upgrade policy

A new upstream stable release triggers review, not an automatic runtime change:

```text
new stable release
     ↓
compatibility/security review
     ↓
implementation + tests
     ↓
capability metadata update
     ↓
OWA release
```

Draft, preview, RC, and editor-draft specifications must not become the default production baseline.

Because the OWA public contract is still stabilizing, legacy protocol generations are removed during migration rather than maintained as compatibility layers.
