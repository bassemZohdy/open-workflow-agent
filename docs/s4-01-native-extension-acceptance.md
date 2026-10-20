# S4-01 native metadata extensions

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
first consumer remains unqualified until its focused and final batch gates
pass; ADK continues to reject the required extension. Ordinary descriptive
metadata remains inert. Optional declarations are retained as metadata and do
not enable behavior; users must only mark an extension optional when the
workflow remains correct without it. Each feature must implement its semantics
in an engine adapter, advertise the supported exact version, and add
portable/equivalence evidence where required.

Focused Python checks are in `tests/core/test_workflow.py`. They cover the
declaration grammar and finite limits, exact-version capability binding,
missing-capability rejection, and rejection of unsupported scoped
declarations. This record does not close S4-01's broader feature inventory or
independent JavaScript qualification.
