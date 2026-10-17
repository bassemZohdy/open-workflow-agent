# Captured JSON Schema resources

Open Workflow 1.0.3 data definitions support inline Draft 2020-12 schemas and
`schema.resource.endpoint`. OWA resolves approved resources during asynchronous
workflow or sandbox compilation. The public DSL/schema remains unchanged.
Synchronous compilation supports inline closures and rejects remote resources.

External schemas are disabled by default. A deployment opts in through
`workflow.external_resources`, independently of external function catalogs.
Each policy requires an allowed host, the exact HTTPS retrieval URL in
`allowed_endpoints`, and that URL's SHA-256 of the original response bytes in
`integrity_pins`. Trailing slashes are significant. Pinning cannot be disabled.
The URL must match exactly one configured policy. A workflow declaration cannot
approve a URL or supply credentials. For example, deployment configuration is:

```yaml
workflow:
  external_resources:
    schemas:
      allowed_hosts: [schemas.example.org]
      allowed_endpoints: [https://schemas.example.org/input.json]
      integrity_pins:
        https://schemas.example.org/input.json: <64-hex-digit-sha256>
      timeout_seconds: 10
      max_response_bytes: 1000000
```

The corresponding workflow input definition is:

```yaml
input:
  schema:
    resource:
      endpoint: https://schemas.example.org/input.json
```

Fragments select a schema inside a captured document. Relative references resolve
against document/resource identifiers; each additional retrieval URL needs its own
approval and pin. Anchors, dynamic anchors, pointers and finite document cycles
are resolved through a Draft 2020-12 registry. Missing fragments, non-schema
targets, conflicting resource identifiers, invalid schemas and other dialects
fail admission. Fields named `$ref`,
`resource`, or `endpoint` inside application data, schema constants/defaults or
examples do not trigger resource loading.

Retrieval enforces TLS verification, public destinations with DNS address pinning,
no redirects or ambient proxy configuration, bounded responses and timeouts.
Optional deployment security profiles provide host-bound authentication. Safe
JSON-compatible YAML loading rejects duplicate/non-string keys, native objects,
cycles, invalid UTF-8 and non-finite values. Source and expanded data are limited
to 16,000,000 bytes, 100,000 nodes including keys and depth 64. The captured schema
closure is limited to 32 documents and 16,000,000 bytes. Deployment cache policies
support TTL, expiry, bounded entries and ETag/Last-Modified revalidation; a 304
response cannot invent an uncached document.

The internal immutable plan captures resource URI, original-byte digest and safe
document content. These affect the common workflow fingerprint. Original workflow
and imported task descriptors retain their resource declarations. Derived schema
references and the live validation registry remain private. Execution rechecks
current approval/pins before effects, rebuilds its registry from captured content
and performs no schema retrieval. Revoked approval or changed pins reject the
captured plan; changed schema content requires a new plan/fingerprint. Native
checkpoint snapshots never serialize the registry. Workflow-version migration
policy remains S2-09.

Root, reusable task and captured catalog task input/output/export use the common
validation order on ADK and LangGraph. Tests cover valid/invalid data, policy and
integrity failures, credential ownership, closure limits, document cycles,
revalidation, immutable capture and native checkpoint restart. Full proposed
inventory tests, independent JavaScript/TypeScript and full OWS conformance remain
later-stage work.
