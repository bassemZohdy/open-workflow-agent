# Reuse the existing work without importing incorrect semantics

| Material | Useful parts | Required canonical adaptation | TODO |
|---|---|---|---|
| Specs LangGraph compiler | StateGraph nodes, conditional routing, loop edges, isolated fork channels/reducers, static RetryPolicy lowering | Bind native checkpointers; replace first-declared competitive result with first-completed durable winner/cancellation; preserve OWS data and scopes | S1-05, S2-01 through S2-04 |
| Specs core IR/loaders | Frozen typed plan, parser/loader split, semantic validation and catalog bindings | Canonical OWS version/schema and internal IR; do not expose a second DSL | S1-01, S1-04, S1-07 |
| Specs runtime/protocols | Lifecycle, HTTP safeguards, MCP session limits, retry and telemetry tests | Existing common services and SandboxManager; native task boundaries; durable waits/events | S2-03 through S2-09, S3-01, S3-06, S4-05 |
| Specs AgentSpec/ADK | Separate agent definitions, provider/role bindings, native compiler/runner tests | Agent capability stays composable inside authoritative workflows; map compatible bindings to canonical config | S3-07, S3-08, S4-08 |
| Standalone ADK translator/tasks | Workflow/FunctionNode/JoinNode construction; reusable handlers and graph tests | Preserve OWS semantics, identity, errors, cancellation and canonical execution boundary | S3-08 |
| Standalone ADK integrations | Provider/memory/broker/protocol adapters and hostile-expression/security fixtures | Shared contracts, pinned SDK versions, optional deployment-owned providers; remove duplicate persistence/security surfaces | S3-01 through S3-06, S4-05, S4-10 |
| Standalone ADK tooling | Diagnostics, generation, visualization, exports/imports, benchmarks and CLI tests | Decide compatible old imports/commands; qualify generated workflows and benchmarks | S4-06, S4-08 |
| Generic LangGraph patterns | Eight named behavior families, retained as independently authored descriptions | Standard OWS recipes first; declared native extensions only when required; never activate unsafe approval defaults | S3-07, S4-01, S4-02 |

The pinned source paths, exact-byte destinations and hashes are in
[source-manifest.json](source-manifest.json). Both Apache source trees are
frozen `.source` references. They are not imported, executed, installed or used
as normative conformance oracles. Generic source stays at origin because no
license grant was detected.

The specs compiler's custom leaf checkpoint store is not a native LangGraph
checkpointer. Its waits sleep in-process; listen reads supplied event lists;
competitive joins select the first declared branch. These are explicit repair
requirements. The ADK reference uses JSONata; expression literals and task-data
rules must be reconciled with canonical jq semantics rather than copied.

Keep Python and JavaScript delivery/evidence separate. Preserve each upstream
source and change notice when porting a modified implementation. The full
tracker retains all gaps while each small implementation slice replaces a
reference pattern with tested canonical code.
