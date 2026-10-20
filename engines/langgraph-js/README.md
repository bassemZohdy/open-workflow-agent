# LangGraph JavaScript foundation

This isolated package establishes a pinned Node 24 / strict TypeScript
toolchain around `@langchain/langgraph` 1.4.21. The package can parse a YAML or
JSON workflow definition and validate it against the repository's unchanged
official Open Workflow 1.0.3 schema. The loader verifies the reviewed schema
SHA-256 pin before use. A deterministic native-graph smoke test remains
separate from workflow validation.

`loadWorkflowDefinition(source)` returns a validated definition or raises a
`WorkflowDefinitionError` with a bounded diagnostic. Schema validity is only
the first admission layer: this package does not compile or execute workflows,
validate the OWA Portable Profile, provide an engine adapter, or establish
parity with Python. Unsupported execution features must remain explicit when
those layers are implemented; schema-valid input alone must never imply that a
feature is executable.

The package is optional and is not included in production images, release
artifacts, or Python environments. Its future engine API must accept canonical
Open Workflow 1.0.3 inputs at its boundary; any typed internal plan must remain
private to this package and must not cross the public API or protocol boundary.
Python-only and JavaScript-only behavior still require separate evidence.

Run `npm ci` followed by `npm test` from this directory to check pinned schema
loading/validation and the native graph smoke test. The required Node 24 CI job
runs these checks and audits production dependencies; this validates the loader
foundation only, not JavaScript execution parity.
