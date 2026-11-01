# LangGraph JavaScript foundation

This isolated package establishes a pinned Node 24 / strict TypeScript
toolchain around `@langchain/langgraph` 1.4.21. The package can parse a YAML or
JSON workflow definition and validate it against the repository's unchanged
official Open Workflow 1.0.3 schema. The loader verifies the reviewed schema
SHA-256 pin before use. A deterministic native-graph smoke test remains
separate from workflow validation.

`loadWorkflowDefinition(source)` returns a validated definition or raises a
`WorkflowDefinitionError` with a bounded diagnostic. It rejects source above
1 MiB measured as UTF-8 before parsing; this local admission bound does not
replace deployment request limits. Schema validity is only
the first admission layer; schema-valid input alone does not imply that a feature
is executable. This package does not provide the OWA Portable Profile, public
invocation identity or a production-qualified engine adapter.

`executeLiteralWorkflow(source, input)` is an experimental execution component.
It validates the complete official definition, derives a typed immutable private
program, and builds actual LangGraph nodes for literal `set`, inline `raise`, nested `do` and
`try`/`catch` (literal error filters only), non-competing `fork` tasks and jq 1.8.2
`${ }` expressions in `set` values `switch` with jq `when` conditions and `for` loops. Fixed `continue`, scope-local named transitions,
`exit`, and workflow `end` are supported. Whole-definition admission precedes native graph construction.
Missing source generates the canonical one-task `agent:1.0.0@default` workflow,
which explicitly rejects because catalog calls are outside this component.

The profile rejects expressions outside `set` values, workflow-state variables
(`$workflow`, `$context`, `$input` and similar), data transforms/schemas, calls,
other task types,
native extensions, persistence and recovery. It has no public plan or durable
handle. Values must be finite JSON, at most 1 MiB and depth 64, with integers in
JavaScript's safe range; `${` is evaluated as a jq expression only in `set` values and
rejects elsewhere. jq has no time limit here, so a pathological expression can block
the event loop.
Compilation caps task names at 256 UTF-8 bytes, task references at 8 KiB,
total tasks at 256 and nested scope depth at eight; a shared
512-step budget bounds routing loops. `literalExecutionCapabilities()` exposes
these restrictions. `WorkflowExecutionError.asDict()` projects a bounded common
error code/message/reference without SDK objects or source excerpts. A declared
inline `raise.error` instead projects the literal standard error fields with
the canonical task `instance`, matching the Python contract. Error references and
runtime lookup are unsupported and reject before execution; expression failures
project the common `expression_error` code.

Nine exact shared fixtures exercise this component and the Python adapters.
They cover literal data, sequences, local jumps, nested exit and workflow end;
they do not establish full cross-language parity. Neither checkpointing nor
common invocation identity is qualified by these cases.

Four additional shared fault fixtures cover inline errors, detail/title/type
message precedence, nested propagation and escaped canonical task references.
The current package suite passes 49 tests; each actual Python SDK passes all
13 shared success/fault cases. This finite comparison does not establish full
schema or runtime parity. The pinned JS validator checks JSON Pointer formats;
Python's current default schema validator treats `format` as an annotation.
Fixtures use a valid JSON Pointer for a supplied error instance; broader format
parity remains part of S4-04.

The package is optional and is not included in production images, release
artifacts, or Python environments. Its future engine API must accept canonical
Open Workflow 1.0.3 inputs at its boundary; any typed internal plan must remain
private to this package and must not cross the public API or protocol boundary.
Python-only and JavaScript-only behavior still require separate evidence.

Run `npm ci` followed by `npm test` from this directory to check pinned schema
loading/validation and the native graph smoke test. The required Node 24 CI job
runs these checks and audits production dependencies; this validates the loader
foundation and the bounded literal component, not full JavaScript execution parity.

`Dockerfile.tests` builds an optional Node test image with dependencies and
compiled code installed at build time. It is not a production/release target.

See the [current integration record](../../docs/validation-history/main-integration-2026-10-25.md)
for hosted validation status. The finite actual-native outcome comparison does
not qualify the full engine or promote proposed/schema criteria.
