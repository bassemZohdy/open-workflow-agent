# LangGraph JavaScript foundation

This isolated package establishes a pinned Node 24 / strict TypeScript
toolchain around `@langchain/langgraph` 1.4.21 and a deterministic native-graph
smoke test. It is scaffolding only: it does not load or execute Open Workflow
definitions, implement an engine adapter, or establish parity with Python.

The package is optional and is not included in production images, release
artifacts, or Python environments. Its future engine API must accept canonical
Open Workflow 1.0.3 inputs at its boundary; any typed internal plan must remain
private to this package and must not cross the public API or protocol boundary.
Python-only and JavaScript-only behavior still require separate evidence.

Run `npm ci` followed by `npm test` from this directory to check the pinned
toolchain and native graph smoke test.
