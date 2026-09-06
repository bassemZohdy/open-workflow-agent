# Portable Profile CTK subset

The original Gherkin files are selected from the upstream Open Workflow CTK at commit
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a` (the repository head used during
integration). The adapter executes the upstream `do`, `set`, `switch`, `for`,
`branch`, `raise`, and deterministic data-flow input-, output-, and
non-object-output filtering scenarios plus the upstream HTTP content-output
projection case, plus
selected portable event and built-in catalog-call scenarios, through both
engine packages and compares their
declared outputs, fault details, property-count assertions, task-order
assertions, and event behavior.

The repository-owned `emit.feature`, `listen.feature`, `call.feature`,
`protocol.feature`, `flow.feature`, `policy.feature`, `transform.feature`,
`try.feature`, `input.feature`, `nested.feature`, `sequence.feature`,
`retry.feature`, and `run.feature` extend that pinned selection with deterministic
cases for
capabilities already covered by the common contract suite. Protocol scenarios
use one shared MockTransport per engine so HTTP, MCP, A2A, and OpenAPI calls
exercise the same common boundary without external network access. Retry
scenarios use the deterministic fake model and do not call a provider.

This is an applicable Portable Profile subset, not a claim of full Open
Workflow conformance. The event, built-in catalog-call, protocol, and flow
scenarios exercise the same common services as production. Broader upstream
CTK coverage and protocol error/interop fixtures remain excluded until their
engine-shared contracts are defined.

The pinned upstream subset passed for both engines in GitHub Actions run
[`32831528433`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32831528433). Each engine job uploads test output plus provenance containing
the repository commit, this pinned upstream CTK commit, and SHA-256 hashes for
the selected scenario files. Further expansion is deferred to later backlog
items and must remain limited to scenarios supported by the declared Portable
Profile. In the current worktree, 22 feature files contain 35 scenarios and
`uv run pytest tests/ctk -q` passes 70 executions across the available ADK and
LangGraph engines.
