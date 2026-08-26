# Configuration Reference

Open Workflow Agent uses strict YAML configuration with environment-variable overrides.

Configuration precedence:

```text
built-in defaults < YAML file < environment variables
```

The default YAML path is `/config/agent.yaml`. Override it with `OWA_CONFIG_FILE`.

Unknown properties are rejected to prevent silent configuration mistakes.

## Environment variable format

Nested YAML fields map to environment variables with `OWA__` and double underscores.

Example YAML:

```yaml
server:
  port: 8080
model:
  name: fake/default
```

Equivalent overrides:

```bash
OWA__SERVER__PORT=8080
OWA__MODEL__NAME=fake/default
```

Environment values are parsed as YAML values, so booleans and numbers can be supplied naturally.

> YAML values are not environment-variable templates. A value such as `${DATABASE_URL}` is not expanded by the runtime. Override the field with `OWA__PERSISTENCE__DATASOURCE` instead.

## Full configuration shape

```yaml
agent:
  name: default
  instruction: You are a helpful assistant.
  tools: []

model:
  provider: litellm
  name: fake/default
  temperature: 0.0
  options: {}

workflow:
  path: null
  definition: null
  catalog: []

knowledge:
  path: /knowledge
  database: /data/knowledge.sqlite3
  reload:
    mode: startup
    interval_seconds: 30.0
  chunk_size: 400
  chunk_overlap: 40

embedding:
  provider: fastembed
  model: sentence-transformers/all-MiniLM-L6-v2
  revision: ea78891063587eb050ed4166b20062eaf978037c

memory:
  enabled: auto
  database: /data/memory.sqlite3

persistence:
  datasource: null
  database: /data/runtime.sqlite3

tools: []

server:
  host: 0.0.0.0
  port: 8080
  max_request_bytes: 1048576

observability:
  log_level: INFO
```

## `agent`

```yaml
agent:
  name: support
  instruction: |
    Answer customer questions using available tools.
  tools: []
```

Fields:

- `name`: logical agent name.
- `instruction`: instruction supplied to the configured agent.
- `tools`: reserved list of tool names associated with the agent configuration. Runtime-provided tool bindings also come from knowledge, memory, and `tools` definitions.

## `model`

```yaml
model:
  provider: fake
  name: fake/default
  temperature: 0.0
  options: {}
```

Supported runtime behavior:

- `provider: fake` uses the deterministic built-in model for tests and local validation.
- any non-`fake` provider is resolved through the LiteLLM model adapter.

For LiteLLM-backed use, the optional `model` dependency must be installed. Provider credentials should be supplied with environment variables/secrets expected by the selected LiteLLM provider, not embedded in workflow files.

Example:

```yaml
model:
  provider: litellm
  name: openai/gpt-4.1-mini
  temperature: 0.1
```

The exact provider/model identifier and required secret variables are governed by LiteLLM/provider configuration.

## `workflow`

You can configure either a workflow path or an inline workflow definition.

Path:

```yaml
workflow:
  path: /config/workflow.yaml
```

Inline:

```yaml
workflow:
  definition:
    document:
      dsl: '1.0.3'
      namespace: example
      name: inline
      version: '1.0.0'
    do:
      - answer:
          call: agent:1.0.0@default
```

If neither is supplied, the runtime generates the default one-task workflow.

`workflow.catalog` registers deployment-provided local child workflows for `run.workflow`. External/remote catalogs are currently disabled.

## `knowledge`

```yaml
knowledge:
  path: /knowledge
  database: /data/knowledge.sqlite3
  reload:
    mode: startup
    interval_seconds: 30
  chunk_size: 400
  chunk_overlap: 40
```

Reload modes:

- `startup`: index/reconcile during startup.
- `manual`: reload only through the admin endpoint.
- `watch`: periodically reconcile document fingerprints.

Manual reload:

```bash
curl -X POST http://localhost:8080/v1/admin/knowledge/reload
```

Knowledge is exposed to agents as the `search_knowledge` tool.

## `embedding`

```yaml
embedding:
  provider: fastembed
  model: sentence-transformers/all-MiniLM-L6-v2
  revision: ea78891063587eb050ed4166b20062eaf978037c
```

`sentence-transformers` is still accepted as a migration alias for the provider field, but the packaged implementation uses FastEmbed/ONNX.

## `memory`

```yaml
memory:
  enabled: auto
  database: /data/memory.sqlite3
```

Values:

- `false`: disable memory tools.
- `true`: enable memory and persist it to the configured memory store.
- `auto`: enable memory capability; persistence becomes durable when a datasource/database-backed runtime is configured.

Available memory tools are `add_memory`, `search_memory`, and `delete_memory`.

Memory is separate from engine checkpoint/resume state.

## `persistence`

SQLite/local:

```yaml
persistence:
  database: /data/runtime.sqlite3
```

PostgreSQL:

```yaml
persistence:
  datasource: postgresql://user:password@host:5432/database
```

For production secret injection, keep `datasource` unset in YAML and override it at deployment time:

```bash
OWA__PERSISTENCE__DATASOURCE='postgresql://user:password@host:5432/database'
```

The datasource is shared as a connection target, but subsystems use isolated namespaces/tables. ADK and LangGraph retain their own native durable state rather than sharing engine checkpoint formats.

## `tools`

Supported configured tool types are:

```text
mcp
openapi
a2a
```

General shape:

```yaml
tools:
  - type: mcp
    name: my-tool
    endpoint: https://example.internal/mcp
    options: {}
```

Protocol clients are bounded by runtime policies such as TLS verification, timeouts, redirect handling, response-size limits, endpoint validation, and optional host allowlists.

Configured agent tools are distinct from explicit workflow protocol calls.

## `server`

```yaml
server:
  host: 0.0.0.0
  port: 8080
  max_request_bytes: 1048576
```

`max_request_bytes` applies to invocation/resume and other bounded request bodies enforced by the HTTP middleware.

## `observability`

```yaml
observability:
  log_level: INFO
```

The runtime also emits engine-neutral lifecycle records containing stable workflow/task references where applicable.

## Recommended deployment pattern

Keep non-secret runtime behavior in `agent.yaml`:

```yaml
agent:
  name: support
  instruction: Answer using approved knowledge.

model:
  provider: litellm
  name: provider/model

workflow:
  path: /config/workflow.yaml

knowledge:
  path: /knowledge
```

Inject secrets and environment-specific values separately:

```bash
OWA__PERSISTENCE__DATASOURCE='postgresql://user:password@host:5432/database'
```

Do not place API keys, passwords, or bearer tokens directly in ordinary workflow definitions.
