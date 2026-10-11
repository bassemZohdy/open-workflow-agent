:warning: **Updated.** The model registry moved from `use.models` to
`document.metadata.adk.models`. This ADR now describes the current encoding.

# ADR 0005: Named model bundles use an explicit reference object

## Decision

Agent characteristics accept either a literal model string or
`model: {use: <name>}`. The object form resolves against the workflow's
`document.metadata.adk.models` registry; a bare model name is always treated as a
literal model ID.

## Rationale

An explicit object avoids ambiguity between a provider model ID and a registry
entry while allowing a centrally managed generation configuration bundle.
Placing the registry in `document.metadata.adk` keeps the document valid
OpenWorkflow v1.0.3, because `document.metadata` allows additional properties
and other implementors will ignore the `adk` block.

## Consequences

Named bundle fields are resolved first, task-level fields override the bundle,
and environment variables under `WORKFLOW_MODELS__<NAME>__...` can repoint a
bundle for deployment-specific configuration. The legacy `use.models` registry
was removed; documents using it are rejected with a migration hint.
