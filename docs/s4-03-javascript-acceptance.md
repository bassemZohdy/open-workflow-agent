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

Initial local smoke evidence is recorded after running the pinned package's
`npm ci` and `npm test` commands on Node 24.16.0 / npm 12.0.2. `npm ci`
installed from the committed lockfile, TypeScript compilation succeeded, and
the native-graph smoke test passed (1 test). This evidence verifies the
toolchain and graph wiring only.
