# Architecture

Detailed translation reference for `open-workflow-agent-adk`. [`CLAUDE.md`](../CLAUDE.md) holds the
concise baseline (project, config precedence, stack); this doc expands the pipeline, the task→ADK
mapping, the package layout, and the ADK gotchas implementers must honor. Task status lives in
[`TODO.md`](../TODO.md).

## Package layout and layering

The translator is organized in layers, enforced by an `import-linter` contract wired into CI
(`uv run import-linter lint`):

```text
core (no imports from ops/tools/interop/devtools)
    openworkflow_adk/            loader, models, state, expressions, translator, runtime, …
    openworkflow_adk/registry.py WorkflowRegistry + WorkflowSearchResult (moved from tools under C24)
    openworkflow_adk/durations.py, suspension.py, run_config.py  infra primitives
    openworkflow_adk/adk_compat.py  sole seam over google.adk.workflow._* private APIs
    openworkflow_adk/tasks/      per-task builders (translate into ADK nodes)
services (may import core and each other)
    openworkflow_adk/ops/        history, telemetry, scheduling, polling workers, management
    openworkflow_adk/interop/    cross-runtime formats: Temporal (exports), Airflow/Argo (importers), OpenAPI
    openworkflow_adk/devtools/   diagnostics, generation, optimization, portability, usage, visual
    openworkflow_adk/security/   egress guards, auth, audit, SSO adapters
    openworkflow_adk/resources/  brokers, memory, providers, templates
    openworkflow_adk/tools/      backward-compatible facades + plugins/patterns
```

The `tools` package was split into `interop` and `devtools` under C24; `tools/` now re-exports the
moved modules for existing callers. All private `google.adk.workflow._*` imports route through
`adk_compat.py`, and the nightly canary CI job runs the suite against the latest ADK to detect
upstream drift.

Workflow execution settings are consolidated in the frozen `RunConfig` dataclass
(`openworkflow_adk.run_config`); `run_workflow(..., config=...)` is the modern entry point and the
keyword form remains backward compatible.

## Pipeline

```
workflow.yaml/json
   │  1. load (PyYAML / json)
   ▼
OpenWorkflowDocument (typed pydantic model)
   │  2. validate ADK payload (`metadata.adk`) with Pydantic
   │  3. validate against vendored OpenWorkflow v1.0.3 schema
   │  4. resolve config (env > per-task `metadata.adk.agent` > project defaults)
   ▼
Resolved tasks + agent characteristics
   │  5. translate: each task → ADK node (LlmAgent | FunctionNode | nested Workflow)
   │                  taskList `do` → edge chain; `then`/`switch`/`fork` → edges/routes
   │                  JSONata expressions (${…}) → expression evaluator over ctx.state
   ▼
adk.Workflow(edges=…, state_schema=…)
   │  6. run via adk.Runner + InMemory/SQLite/PostgreSQL session and run-history backends
   ▼
events / output
```

**Core extension**: a task carrying agent-characteristics config (model/instructions/tools) compiles
to an `adk.Agent` node; a task without it compiles to a deterministic `FunctionNode`. Tasks without
agent config fall back to project-wide defaults (3-layer precedence — see
[`configuration.md`](configuration.md)).

## Key mapping (OpenWorkflow task → ADK construct)

| OpenWorkflow | ADK |
|---|---|
| `do` taskList | sequential edge chain `("START", t1, t2, …)` |
| `then: continue` | implicit edge to next task |
| `then: end`/`exit` | terminal node (no out-edge) |
| `then: <namedTask>` | explicit edge to that named task (goto) |
| `switch` | router `FunctionNode` yields `Event(route=case)` + `RoutingMap` edge; default → `DEFAULT_ROUTE` |
| `fork` (no compete) | fan-out `(joinPoint, (branchA, branchB))` → `JoinNode` fan-in |
| `fork.compete: true` | race: custom coordinator node, first completion wins, cancel siblings |
| `try`/`catch` | wrapper `FunctionNode` runs try-tasks via `ctx.run_node`, catches, runs catch-tasks; `retry` → `RetryConfig` |
| `for` (+`while`) | loop `FunctionNode`: iterate collection, dispatch `do` per item via `ctx.run_node` |
| `wait` (duration) | `FunctionNode` → `asyncio.sleep(duration)` |
| `set` | `FunctionNode` writes `ctx.state` (state_delta) |
| `raise` | `FunctionNode` raises typed error (caught by enclosing `try`) |
| `emit`/`listen` | broker-adapter interface (pluggable); emit publishes, listen awaits |
| `call: http` | `FunctionNode` + httpx |
| `call: grpc`/`openapi`/`asyncapi` | `FunctionNode` + protocol client |
| `call: a2a`/`mcp` | reuse ADK a2a/mcp tooling |
| `call: function` (named) | lookup in `use.functions` registry → `FunctionNode` |
| `run: shell`/`script`/`container`/`workflow` | subprocess / code-exec / docker SDK / nested Workflow |
| `if` (taskBase) | guard: skip node if expression false (conditional edge or in-node check) |
| `input`/`output`/`export` | node_input shaping + `output_key` + `$context` write (JSONata) |
| taskBase `timeout` | `FunctionNode(timeout=…)` / `RetryConfig` |
| `use.retries` | `RetryConfig` references |
| `use.authentications` | `AuthConfig` on nodes |
| **task with `metadata.adk.agent`** | `adk.Agent`(model, instruction, tools, output_schema, output_key) |
| `document.metadata.adk.models` (extension) | named `ModelSpec` registry; agent `model: {use: name}` resolves a model + generation-config bundle |
| `document.metadata.adk.providers` (extension) | named `ProviderConfig` registry; a model references one; resolved to an ADK `BaseLlm` via the provider factory |
| `document.metadata.adk.memories` (extension) | named `MemoryConfig` registry; agent `memory: {use: name}` resolves to an ADK `BaseMemoryService` |

The broker interface also ships CloudEvents 1.0 adapters for Redis Streams,
Kafka, RabbitMQ, and NATS. Their client SDKs are lazy and available through
the optional `brokers` extra; tests can inject a transport client directly.

## ADK gotchas to honor

- `mode='task'` `LlmAgent`s **cannot** be static graph nodes; the task builders validate this
  constraint before constructing the workflow graph.
  Task-mode agents must be chat sub-agents or dispatched via `ctx.run_node`. Default agent-tasks to
  `single_turn` when used as graph nodes.
- A workflow allows **exactly one** terminal output; multiple terminal-node outputs raise. `fork`
  without join and multiple `then: end` branches must funnel through a `JoinNode`.
- `state_schema` (pydantic BaseModel) declares workflow state; every `FunctionNode` param (except
  `ctx`/`node_input`/`self`) must be a field on it (validated at build time).
- `output_key` on an `Agent` persists its reply into `ctx.state` — maps to taskBase `output`.
- OpenWorkflow expressions are **JSONata** (`${ .x }`, `$workflow.…`); ADK instruction placeholders
  are `{var}` from state. Two different mechanisms — bridge, don't conflate.
