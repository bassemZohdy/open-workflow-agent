# Native LangGraph task execution

The LangGraph adapter lowers every admitted task sequence to a native
`StateGraph`: one named node per task and native conditional edges for
successors, switch/catch overrides, named transitions and scope termination.
Core derives immutable scope/task plans and owns each task's input/body/output/
export pipeline, errors, catalog and services. Core imports no framework SDK.
Nested `do`, loop bodies, try/catch scopes and fork branches each run as native
scope graphs. Loop/fork orchestration remains common. Ordered results and durable
competitive decisions are verified by S2-01/S2-02; declared native channel
reducers remain Stage 4 work. See [fork behavior](workflow-forks.md).
Reusable task bodies also lower to native scopes per call site, while public
descriptors retain the original definition pointers. See the
[reusable definition contract](reusable-definitions.md).

The adapter uses the pinned SDK's native memory, SQLite or PostgreSQL saver.
Checkpoints contain JSON text for public execution data/bindings plus private
routing and scope-occurrence counters. Live cancellation/wait controls,
services, handles, evaluators, asyncio tasks and internal plans are excluded.
JSON null is ordinary snapshot data. Functional API compatibility helpers
retain their private null marker for direct callers, but engine invocations
use the task graphs.

Private checkpoint identities bind the common invocation id, workflow
fingerprint, actual scope reference and scope occurrence. Two invocations in
one session never share a native thread. Native thread/node/checkpoint ids,
channels and graph objects are absent from public results and lifecycle
records. Native storage/graph failures become sanitized common errors.
`features.nativeTaskExecution` reports actual native node availability and the
1,000,000-step native guard per scope; this guard is not an OWS language limit.
Minimal environments without the selected optional native saver retain the
common interpreter and report native nodes unavailable. Native SDK and saver
availability are checked independently: PostgreSQL needs its own saver,
without requiring SQLite to be installed.

Saver initialization serializes the pinned SDK migrations across connections
and processes, using the same setup lock as private native memory providers.
PostgreSQL takes a schema-scoped session lock with short nonblocking probes;
this permits the SDK's concurrent index creation without retaining a waiting
snapshot. SQLite uses an adjacent `<checkpoint-database>.setup.sqlite3` lock
database across SDK commits. A ten-second asynchronous deadline bounds migration
and lock waiting. Connection establishment retains its existing boundary.
The lock is released before workflow tasks run; it does not serialize whole
invocations. The lock database contains no checkpoint or workflow records.
SDK checkpoint formats and the existing common ownership fences are unchanged.
See the [native memory and checkpoint setup qualification](s4-02-native-memory-acceptance.md).

Native checkpoints are committed synchronously between tasks. On restart the
adapter resumes pending native tasks using their stored data and original
workflow input. Completed sequential tasks and completed child scopes are not
replayed. A task interrupted before its checkpoint may repeat, so the existing
common operation/idempotency boundary still applies. This milestone does not
claim exactly-once effects, native/common-record reconciliation after every
crash, durable waits/events, or in-flight migration
from the earlier Functional API envelope. S2-03/S2-08/S2-09 own those gates.
The native checkpoint key/format is adapter-private and versioned; older
checkpoints are preserved but cannot silently resume through this compiler.
The common competitive decision store separately prevents branch replay after
a winner is committed, including interruption before the parent checkpoint;
that S2-02 guarantee does not reconcile general pending external actions.

All calls, including executable operations, still use common services and
SandboxManager. The adapter creates no subprocess, container or pod execution
path. ADK and optional Agent Framework continue their existing native bridges
and execute the identical shared semantic fixtures. No independent JS or full
Open Workflow conformance is claimed.

Evidence: [native topology/checkpoint/restart tests](../tests/langgraph/test_native_task_graph.py),
[shared data pipeline](../tests/contract/test_data_pipeline.py),
[shared scoped control flow](../tests/contract/test_scoped_flow.py),
[derived immutable plan tests](../tests/core/test_workflow.py), and the existing
native/contract/CTK, PostgreSQL and runtime-image acceptance gates.

The implementation follows the official [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
and [persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
contracts for native task nodes, conditional edges and saver checkpoints.


Captured catalog protocol tasks have their own derived call scope. Public
references identify the original workflow call; imported task descriptors
contain captured content. A completed import scope recovers without repeating
the protocol action. Synchronously compiled legacy catalog registrations use
the outer task as their native checkpoint unit and retain the registry's
mutable content contract; they do not qualify frozen import replay identity.
