# S4-03 JavaScript/TypeScript acceptance status

The initial optional package foundation is present under
[`engines/langgraph-js`](../engines/langgraph-js/README.md). It pins Node 24,
strict TypeScript, npm, and `@langchain/langgraph` 1.4.21, with a deterministic
native-graph smoke test.

This is scaffolding only. It does not implement an Open Workflow 1.0.3 engine,
consume shared contract fixtures, or qualify any Python/JavaScript behavior as
equivalent. No JavaScript-only or shared capability is promoted by this note.
The package remains outside production images and releases. S4-03 stays open
until the independent engine implementation, relevant inventory tests, and
repository contract/native acceptance gates are complete and recorded.

An additional authored loader slice caps workflow source at 1 MiB of UTF-8
bytes before YAML parsing. Its intended deterministic boundary cases are the
exact limit and one byte above it. This admission bound does not qualify runtime
request limits, execution behavior, or cross-language parity. The Node 24
package suite, including the loader cases, passed locally (10 tests) for the
consolidated batch; exact-source hosted qualification remains pending.

A second authored loader slice mirrors the Python definition-admission
rejections for duplicate YAML/JSON mapping keys, cyclic aliases, implicit
timestamp scalars, and non-finite numeric values. These checks apply only to
definition parsing and JSON-compatible input admission. Duplicate keys and
cyclic aliases receive the same bounded parse-error surface; repeated acyclic
aliases remain allowed. These checks do not implement workflow semantics or
establish independent engine parity. The new shared-fixture cases were included
in the passing Node 24 suite; exact-source hosted qualification remains pending.

Initial local smoke evidence is recorded after running the pinned package's
`npm ci` and `npm test` commands on Node 24.16.0 / npm 12.0.2. `npm ci`
installed from the committed lockfile, TypeScript compilation succeeded, and
the native-graph smoke test passed (1 test). This evidence verifies the
toolchain and graph wiring only.

The consolidated local batch also passed the production dependency audit.
Neither result implements workflow compilation or execution, promotes a shared
capability, or closes S4-03; its independent execution engine and parity gates
remain open.
