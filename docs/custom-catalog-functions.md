# Custom Catalog Functions

Open Workflow Agent resolves custom catalog functions from deployment-trusted
catalog endpoints. A catalog function is a versioned metadata/implementation
definition; it is not a Python plugin and it must not contain a remote script.
The runtime fetches the definition before readiness, validates its trust policy,
and translates the declared protocol call through the common protocol services.

## Repository layout

A catalog endpoint exposes one file for each exact function reference:

```text
catalog-root/
└── functions/
    └── summarize/
        └── 1.0.0/
            └── function.yaml
```

The corresponding workflow reference is `summarize:1.0.0@partner`. Function
names use lowercase letters, digits, and hyphens; versions are exact semantic
versions; and the catalog alias is the name under `use.catalogs`.

## Function definition

The descriptive fields make a function discoverable and document its contract.
The executable fields are `call` and `with`:

```yaml
name: summarize
version: 1.0.0
namespace: partner
description: Summarize a supplied document.
input:
  type: object
  required: [text]
  properties:
    text:
      type: string
output:
  type: object
  properties:
    summary:
      type: string

call: http
with:
  method: post
  endpoint: https://api.example.com/summarize
  body:
    text: ${ .text }
```

Supported protocol calls are `http`, `mcp`, `a2a`, and `openapi`. The `with`
object is passed through the selected common protocol adapter; use the same
bounded request shape documented for that protocol. Expressions such as
`${ .text }` select values from the task input.

Remote `run` definitions, `$ref` expansion, and authorization headers in the
function definition are rejected. A catalog function is therefore a declarative
protocol call, not an execution or credential escape hatch.

## Deployment trust policy

Catalogs are disabled unless the deployment configures an alias and trust policy:

```yaml
workflow:
  external_catalogs:
    partner:
      allowed_hosts: [catalog.example.com, api.example.com]
      allowed_endpoints:
        - https://catalog.example.com/root
      timeout_seconds: 10
      max_response_bytes: 4000000
      verify_tls: true
      follow_redirects: false
      require_integrity_pin: true
      integrity_pins:
        summarize:1.0.0@partner: <64-hex-sha256>
      authentication:
        security_profile: catalog-reader

security:
  profiles:
    catalog-reader:
      type: bearer
      token:
        from_env: OWA_CATALOG_READER_TOKEN
```

The workflow selects the trusted alias and the exact function:

```yaml
document:
  dsl: 1.0.3
  namespace: example
  name: summarize-document
  version: 1.0.0

use:
  catalogs:
    partner:
      endpoint:
        uri: https://catalog.example.com/root

do:
  - summarize:
      call: summarize:1.0.0@partner
      with:
        text: ${ .document }
```

The endpoint in the workflow identifies the catalog location, while the
deployment policy owns allowed hosts/endpoints, TLS and redirect behavior,
response limits, caching, integrity pins, and credentials. Credentials must be
named security profiles or deployment environment references; never put tokens
in a workflow or `function.yaml`.

## Verification and rollout

Catalog functions are fetched before readiness is announced and are
integrity-verified when a matching pin is configured or required.
`GET /v1/capabilities` reports sanitized catalog policy/state without secrets.
Use the external-catalog contract tests while authoring a catalog:

```bash
uv run pytest tests/core/test_external_catalog.py \
  tests/contract/test_contract_external_catalog.py -q
```

For rollback, remove the workflow's `use.catalogs` reference and the corresponding
deployment policy. Local built-in functions (`agent:1.0.0@default` and
`llm:1.0.0@default`) remain available without an external catalog.
