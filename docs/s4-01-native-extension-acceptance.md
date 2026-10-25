# S4-01 native metadata extensions

## Exact-source qualification d72781f

S4-01 is verified for its finite profile at `d72781f86053a7fd77e7b9df7c3a74c262923136`. Versioned native metadata declaration and exact capability admission, required/optional semantics and engine/backend checks with unsupported required requests rejected before mutation; real channel/Command witnesses and ADK rejection.

This qualifies the declaration/admission boundary, not the wider S4-02 native features or independent JavaScript engine.

All six source-matched workflows pass; [the retained results](validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f) include actual principal SDKs, both databases, platform/process, image, security and packaging checks. This is not full OWS/proposed-test conformance.

## Historical progress and acceptance design

The notes below predate the qualification above; their prior pending/failed states are preserved.

S4-01 establishes a versioned declaration and engine admission boundary for
future native features. It keeps the pinned Open Workflow 1.0.3 schema
unchanged and uses its ordinary `document.metadata` object. The internal
execution plan carries parsed requirements privately; it is not part of an API
response or a second authoring format.

The current declaration is:

```yaml
document:
  dsl: "1.0.3"
  namespace: example
  name: sample
  version: 1.0.0
  metadata:
    owa:
      nativeExtensions:
        - name: langgraph.channels
          version: 1.0.0
          required: true # defaults to true
```

Names are lowercase namespaced identifiers and versions are exact
`major.minor.patch` values. At most 32 declarations are allowed; names are
limited to 128 characters and versions to 64 characters. Each declaration may contain only `name`,
`version`, and `required`; scope selectors and version ranges are rejected.
Duplicate name/version pairs and malformed declarations fail semantic
admission. The selected engine must advertise the exact `name@version` in its
`nativeExtensions` capability before execution or resume admission. Missing
or mismatched required capabilities fail with `unsupported_workflow_feature`.

This stage provides the shared declaration and admission mechanism; feature
semantics remain owned by engine adapters. The S4-02 working-tree slice now
implements and advertises `langgraph.channels@1.0.0` for LangGraph only. That
first consumer's focused core and native LangGraph tests pass. ADK continues to
reject the required extension. The core withdrawal/recheck admission
regression also passed in the corrected consolidated local suite. Exact-source
hosted qualification remains pending. Ordinary descriptive
metadata remains inert. Optional declarations are retained as metadata and do
not enable behavior; users must only mark an extension optional when the
workflow remains correct without it. Each feature must implement its semantics
in an engine adapter, advertise the supported exact version, and add
portable/equivalence evidence where required.

Focused Python checks are in `tests/core/test_workflow.py`. They cover the
declaration grammar and finite limits, exact-version capability binding,
missing-capability rejection, and rejection of unsupported scoped
declarations. A deterministic regression now also checks that repeated
capability verification rejects a required extension if the engine no longer
advertises its exact version after an earlier successful check; the regression
passed in the consolidated local suite. This record does not close S4-01's broader
feature inventory or independent JavaScript qualification.
