# Workflow definition admission

OWA Portable Profile v1 uses DSL `1.0.3` with the unmodified, digest-pinned
[upstream schema snapshot](../resources/open-workflow/1.0.3/SCHEMA_SOURCE.md).
The DSL release is prerelease; the later schema snapshot and release tag
are distinct pins. `/v1/capabilities` exposes their provenance in
`workflowProfile`, without exposing the internal plan. This is a bounded
profile, not full Open Workflow conformance.

Admission order is JSON-compatible loading, official schema validation,
profile/deployment capability validation, then immutable normalization.
YAML and JSON preserve declaration order and have the same admitted data.
Duplicate mapping keys, non-string keys, cyclic aliases, non-finite numbers,
Python objects and nesting deeper than 64 levels are rejected. Task names
must be unique within their scope. Document identity, title, summary, tags
and metadata survive normalization; clients cannot mutate the plan by
mutating their source or a returned source copy.

`evaluate.language` supports only `jq`. Both `strict` (default) and `loose`
modes use the same pinned jq implementation. See [expression semantics](workflow-expressions.md)
for recognition, types, errors and execution limits. The
[data pipeline](workflow-data-flow.md) documents schema/transformation/export
ordering and descriptor scope separately from jq syntax.

Currently rejected reusable components are `use.authentications`, `errors`,
`extensions`, `functions`, `retries`, `secrets` and `timeouts`. Named errors
and named timeouts are rejected rather than interpreted as message text or
durations. Use standard inline errors and timeouts in this profile. Reusable
resolution is staged in S1-07. Catalog calls retain the existing
deployment-controlled trust gate; declarations cannot authorize a remote
catalog themselves. Metadata is inert; native extension admission is staged
in S4-01.

Data schemas support inline JSON Schema 2020-12 (`json` or `json:2020-12`),
including boolean schemas and local fragment references. Malformed inline
schemas fail admission. External schema resources/references and other
formats fail the capability gate without a network request. Credentials
are supplied through runtime security profiles, never ordinary workflows.
Inline OWS endpoint authentication policies are rejected because the common
protocol clients cannot interpret them. A field named `authentication` inside
an application request body remains data, not protocol configuration.

The existing single-event listener supports `data`, `envelope` and `raw`
reads. Iterators and correlation are rejected until implemented. Registered
child workflows remain synchronous; background runs and `run.return`
selection are rejected. Script, shell and container operations require the
existing deployment-controlled SandboxManager boundary; disabled containers
fail with the common unsupported-feature contract. Sandbox admission applies
the same background/return gate before replacing executable runs for common
profile validation.

Schema violations use `workflow_schema_error`; admitted-schema features
outside the profile use `unsupported_workflow_feature`; invalid semantic
definitions use `workflow_semantic_error`. Nested capability failures retain
the original task pointer. Definition failures happen before invocation or
external effects. Later stages must replace a rejection with implementation
and evidence, rather than silently accepting an ignored field.

S1-01 evidence is in `tests/contract/test_definition_admission.py`, the existing
workflow/catalog/protocol baseline tests, and the common contract/CTK suites.
Tracker rows retain independent JS and later-stage status. Proposed inventory
tests remain proposed unless their complete acceptance criteria are executed.
