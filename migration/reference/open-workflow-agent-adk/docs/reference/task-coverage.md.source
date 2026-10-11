# Task & feature coverage

Baseline: OpenWorkflow Specification v1.0.3 (vendored schema). This matrix
tracks runtime support per task kind and cross-cutting feature.

## Task kinds

| Task kind | Status | Notes |
|---|---|---|
| `set` | Supported | state writes via ADK state deltas |
| `wait` | Supported | duration dict; long waits suspend durably when history is configured |
| `raise` | Supported | inline errors and reusable `use.errors` references; `${...}` fields evaluated against `$workflow.definition`/`$context` |
| `switch` | Supported | routed ADK edges; string and `when` conditions; honors `if` |
| `for` | Supported | `each`/`at`/`in` plus `while` guard |
| `fork` | Supported | fan-out/join and competing race (`compete: true`) |
| `try`/`catch` | Supported | error filters (`catch.errors.with`), retry policies, `as` error capture, self-heal extension |
| `do` (nested) | Supported | sequential nested task groups |
| `emit` | Supported | broker publish (in-memory, Redis Streams, Kafka, RabbitMQ, NATS) |
| `listen` | Supported | `one`/`any`/`all` consumption, expression or nested `until`, `foreach` iteration, `correlate` filters, `read: data/envelope/raw` |
| `call: http` | Supported | expressions, output modes, redirect policy, auth policies |
| `call: openapi` | Supported | operation lookup and parameter binding |
| `call: grpc` | Supported | egress/TLS/proto pinning, generated stubs |
| `call: asyncapi` | Supported | broker-backed publish/subscribe |
| `call: a2a` | Supported | agent-to-agent calls |
| `call: mcp` | Supported | MCP tool invocation (stdio allowlist, gRPC) |
| `call: function` | Supported | Python function registry (programmatic and `use.functions`) |
| `run: shell` | Supported | subprocess output modes, sandbox limits |
| `run: script` | Supported | Python and JavaScript (`node`), inline or local source |
| `run: container` | Supported | Docker with hardened defaults (volume allowlist, `network: none`, port/resource caps) |
| `run: workflow` | Supported | nested subflow execution via `WorkflowRegistry` |
| agent extension | Supported | `metadata.adk.agent` → ADK `LlmAgent`; injected model factory enables deterministic tests |

## taskBase fields (apply to every task kind)

| Field | Status | Notes |
|---|---|---|
| `if` | Supported | falsy evaluation skips the task (switch falls back to default route) |
| `input.from` | Supported | filters the task's input expression |
| `output.as` | Supported | transforms the task's output |
| `export.as` | Supported | merges an evaluated object into workflow context |
| `timeout` | Supported | inline or `use.timeouts` reference; enforced for all task kinds |
| `then` | Supported | routing via translator edge construction |

## `use` registries

| Registry | Status | Notes |
|---|---|---|
| `authentications` | Supported | basic/bearer/OAuth2/OIDC policies |
| `secrets` | Supported | environment resolution with redaction |
| `retries` | Supported | reusable retry policies referenced by `catch.retry` |
| `errors` | Supported | reusable error definitions referenced by `raise.error` |
| `timeouts` | Supported | reusable timeout definitions referenced by `task.timeout` |
| `functions` | Supported | document-level function task registry |
| `extensions` | Supported | `extend` (kind or `all`), `when`, `before`/`after` task injection |
| `catalogs` | Supported | qualified calls resolve versioned `function.yaml` resources from local, GitHub, or GitLab catalog roots |

## Retry policy details

`catch.retry` supports inline policies or `use.retries` references with:
`delay` (duration), `backoff` (`constant`/`linear`/`exponential` with `ratio`),
`jitter` (`from`/`to`), `limit.attempt.count`, `limit.duration`, and
`when`/`exceptWhen` runtime expressions evaluated with `$error` and `$context`.
`limit.attempt.count` is the total number of attempts, including the initial
execution (`count: 1` means no retry). The ADK self-heal extension has an
independent budget, so a healer may request another attempt after the retry
policy is exhausted. Error filters accept the spec spelling `details` and the
common singular alias `detail`.

## Workflow-level features

| Feature | Status | Notes |
|---|---|---|
| document `input.from` / `output.as` | Supported | filters run input / shapes final output |
| `schedule` | Supported | `every`, `cron`, `after`, `on` (event-driven) |
| durable suspension/resume | Supported | Postgres/SQLite history backends, region pinning |
| production serving | Supported | FastAPI server, polling worker, metrics, dashboard |
