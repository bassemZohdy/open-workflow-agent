# open-workflow-agent-adk

Run declarative OpenWorkflow documents as reliable, observable AI workflows
with [Google ADK](https://google.github.io/adk-docs/). Describe the business
process in YAML or JSON, combine deterministic steps with AI agents, and run
the same workflow from the command line or Python.

## Why use it?

This project is useful when an AI feature is more than a single prompt and
needs repeatable steps, control flow, integrations, or human decisions.

- Keep workflow logic readable and reviewable as YAML/JSON instead of burying
  it in application code.
- Mix ordinary operations such as setting state, calling HTTP services, waiting,
  looping, branching, and emitting events with ADK agent tasks.
- Run full OpenWorkflow v1.0.3 task semantics: conditional tasks (`if`), input/
  output/export filters, timeouts, catch retry policies with backoff, error
  filters, event correlations, and `use.extensions` task injection — see the
  [task coverage matrix](docs/reference/task-coverage.md).
- Compose multiple agents into a predictable sequence and pass state between
  them.
- Pause for an event or human input and resume later, including after a process
  restart when persistent session/history storage is configured.
- Choose model providers per workflow or task, including ADK-native models and
  OpenAI-compatible, Anthropic, and Bedrock integrations.
- Inspect and validate a workflow before running it, and produce a plan or
  Mermaid graph for review and operations.
- Add memory, event brokers, audit logging, telemetry, usage limits, and
  backpressure as the workflow moves toward production.

Typical uses include approval flows, retrieval-augmented generation, document
processing, research and review pipelines, multi-agent collaboration, and
event-driven automation.

## Choose a workflow flavor

Use **extended mode** when you want inline ADK agents, tools, memory, or teams
through `task.metadata.adk.agent`. The CLI and Python API support `--mode auto|extended`.
See the [workflow flavors guide](docs/flavors.md).

## Quick start

From a checkout of this repository, run the included deterministic example:

```bash
uv sync
uv run owf-adk run examples/hello.yaml
```

The same command works with an installed package by replacing `uv run owf-adk`
with `owf-adk`. For a published release, install the package with:

```bash
pip install open-workflow-agent-adk
```

Pass input data as a JSON object:

```bash
owf-adk run workflow.yaml --input '{"question":"What changed?"}'
```

The repository includes ready-to-try examples for HTTP calls, approvals,
retrieval plus an agent, and multi-agent workflows in [`examples/`](examples/).

## Create a workflow

A workflow is an OpenWorkflow document with an ordered `do` section. This
example stores a value and then branches on it:

```yaml
document:
  dsl: '1.0.3'
  namespace: demo
  name: greeting
  version: '1.0.0'
do:
  - setMessage:
      set:
        message: '"hello from OpenWorkflow"'
  - choose:
      switch:
        - hasMessage:
            when: '${ .message != null }'
            then: ready
        - default:
            then: missing
  - ready:
      set:
        status: '"ready"'
  - missing:
      set:
        status: '"missing"'
```

Add an agent where judgment, language understanding, or generation is needed:

```yaml
do:
  - summarize:
      metadata:
        adk:
          agent:
            model: gemini-2.5-flash
            instruction: Summarize the current input in three concise bullet points.
            output_key: summary
```

This encoding is valid OpenWorkflow v1.0.3: other implementors can parse the
same file and ignore the `metadata.adk` block.

Agent model credentials are supplied by the deployment environment. Defaults
can be configured with `WORKFLOW_` environment variables or a dotenv-style
file passed to `--env`; task-level settings override project defaults. See the
[configuration guide](docs/reference/configuration.md).

## Use it from Python

The runtime returns the events produced during the run, which can be consumed
by an application, logger, or streaming event sink:

```python
from openworkflow_adk import load, run_workflow


async def execute():
    events = await run_workflow(
        load("workflow.yaml"),
        {"question": "What changed?"},
    )
    return events
```

For long-running workflows, configure persistent sessions and run history,
then resume with `resume=True`. Workflows waiting for events or human input
can suspend without keeping a worker busy. The [configuration guide](docs/reference/configuration.md)
covers SQLite, Vertex, memory backends, providers, credentials, and runtime
options.

## Inspect and operate workflows

Use the CLI to understand a workflow before or after deployment:

```bash
owf-adk lint workflow.yaml    # report validation and workflow diagnostics
owf-adk plan workflow.yaml    # print the compiled execution plan
owf-adk graph workflow.yaml   # print a Mermaid graph
owf-adk run workflow.yaml     # execute it
```

For automated scenarios, `owf-adk test workflow.yaml --fixtures cases.json`
runs the workflow against JSON fixtures. Editor integrations can use
`owf-adk diagnostics-server` for diagnostics over stdio.

For HTTP consumers, `owf-adk serve workflow.yaml` exposes `/health`, `/run`,
`/run/stream`, `/openapi.json`, and (with PostgreSQL history) `/metrics`.
The `/health` endpoint is public; all other endpoints require authentication
once `WORKFLOW_SERVER_API_KEY` is set, and serving on a non-loopback host
*requires* it. Database-backed workers and the read-only metrics dashboard are
available as `owf-adk worker start` and `owf-adk dashboard`; see the
[PostgreSQL backend decision](docs/decisions/0009-postgres-backend.md) for the
storage model.

## Extras

The base install is deliberately small. Optional capability groups:

```bash
pip install "open-workflow-agent-adk[server]"   # FastAPI/uvicorn HTTP serving
pip install "open-workflow-agent-adk[brokers]"  # Kafka, RabbitMQ, NATS adapters
pip install "open-workflow-agent-adk[containers]"  # run: container tasks
pip install "open-workflow-agent-adk[grpc]"     # call: grpc tasks
pip install "open-workflow-agent-adk[bedrock]"  # Bedrock provider adapter
pip install "open-workflow-agent-adk[temporal]" # Temporal exporter output
pip install "open-workflow-agent-adk[redis]"    # Redis memory/broker adapters
pip install "open-workflow-agent-adk[database]" # SQLAlchemy-backed memory
pip install "open-workflow-agent-adk[all]"      # everything above
```

Security hardening (fail-closed egress, container isolation, server
authentication, static exec configs) is documented in the [security
reference](docs/reference/security.md).

## Learn more

- [Examples gallery](examples/README.md)
- [Supported extensions](docs/reference/extension-spec.md)
- [Architecture overview](docs/reference/architecture.md)
- [Generation and integration tools](docs/guides/generation.md)
- [Project task list](TODO.md)

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE)
for the full text. The package metadata cites "OpenWorkflow ADK contributors";
see [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution policy.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for repository setup, tests, and code
quality checks.
