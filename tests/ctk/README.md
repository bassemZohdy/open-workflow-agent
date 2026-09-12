# Portable Profile CTK subset

The original Gherkin files are selected from the upstream Open Workflow CTK at commit
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a` (the repository head used during
integration). The adapter executes the upstream `do`, `set`, `switch`, `for`,
`branch`, `raise`, and deterministic data-flow input-, output-, and
non-object-output filtering scenarios plus the upstream HTTP and OpenAPI
content/response-output projection cases and caught/uncaught protocol errors, plus
selected portable event and built-in catalog-call scenarios, through both
engine packages and compares their
declared outputs, fault details, property-count assertions, task-order
assertions, and event behavior.

The repository-owned `emit.feature`, `listen.feature`, `listen-reads.feature`,
`call.feature`, `protocol.feature`, `flow.feature`, `policy.feature`,
`transform.feature`, `try.feature`, `input.feature`, `nested.feature`,
`sequence.feature`, `retry.feature`, `run.feature`, `conditional.feature`,
`for-output.feature`, and `task-schemas.feature` extend that pinned selection
with deterministic cases for
capabilities already covered by the common contract suite. Protocol scenarios
use one shared MockTransport per engine so HTTP, MCP, A2A, and OpenAPI calls
exercise the same common boundary without external network access. Retry
scenarios use the deterministic fake model and do not call a provider.

This is an applicable Portable Profile subset, not a claim of full Open
Workflow conformance. The event, built-in catalog-call, protocol, and flow
scenarios exercise the same common services as production. Broader upstream
CTK coverage, external-authentication cases, and protocol interop fixtures
remain excluded until their engine-shared contracts are defined.

The earlier 70-execution pinned upstream subset passed for both engines in GitHub Actions run
[`32831528433`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32831528433). Each engine job uploads test output plus provenance containing
the repository commit, this pinned upstream CTK commit, and SHA-256 hashes for
the selected scenario files. Broader upstream CTK expansion remains
intentionally deferred and must remain limited to scenarios supported by the
declared Portable Profile. In the current worktree, 26 feature files contain 48 scenarios and
`uv run pytest tests/ctk -q` passes 96 executions across the available ADK and
LangGraph engines. The added cases cover conditional branches, `for` output
accumulation, task input/output schemas, and `listen` read modes.
