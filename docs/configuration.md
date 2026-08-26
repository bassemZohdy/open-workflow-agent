# Configuration Reference

Open Workflow Agent uses strict YAML configuration with environment-variable overrides. For normal use, configuration is mounted into the published GHCR image; changing configuration, workflows, knowledge, memory, persistence, or tools does not require rebuilding the image.

Published images use:

```text
/config     runtime configuration and workflow definitions
/knowledge  mounted knowledge documents
/data       writable runtime state
```

Example image:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
```

The same configuration works with the LangGraph image when the selected features are in the common portable profile.

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

When running a published image:

```bash
docker run --rm \
  -p 8080:8080 \
  -e OWA__SERVER__PORT=8080 \
  -e OWA__MODEL__NAME=fake/default \
  -v "$(pwd)/config:/config:ro" \
  -v "$(pwd)/knowledge:/knowledge:ro" \
  -v "$(pwd)/data:/data" \
  ghcr.io/bassemzohdy/open-workflow-agent-adk:0.1.0
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

- `provider: fake` uses the deterministic built-in model for tests and runtime validation.
- any non-`fake` provider is resolved through the LiteLLM model adapter when the optional `model` dependency is installed in the image.

Example real-provider configuration:

```yaml
model:
  provider: litellm
  name: openai/gpt-4.1-mini
  temperature: 0.1
```

Provider credentials should be supplied with deployment secrets/environment variables expected by the selected LiteLLM provider, not embedded in workflow files.

### Current published-image limitation

The current standard ADK and LangGraph release images do not install the optional LiteLLM `model` dependency. They can be used directly with `fake/default` and the rest of the packaged runtime capabilities. A model-enabled published variant is required before a real LiteLLM provider can be used without building a custom image.

This is an image packaging limitation, not a configuration-format difference.

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

Workflow files are mounted under `/config`; adding or replacing them does not require rebuilding the published image.

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

Knowledge is exposed to agents as the `search_knowledge` tool. Mount source documents into `/knowledge`; no image rebuild is required.

## `embedding`

```yaml
embedding:
  provider: fastembed
  model: sentence-transformers/all-MiniLM-L6-v2
  revision: ea78891063587eb050ed4166b20062eaf978037c
```

`sentence-transformers` is still accepted as a migration alias for the provider field, but the packaged implementation uses FastEmbed/ONNX. The standard release images package the default local model so the normal knowledge path does not require an external embedding service.

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

Persist `/data` using a host volume/PVC when SQLite is used. No source-code change or image rebuild is required to switch between configured persistence targets supported by the image.

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

Configured agent tools are distinct from explicit workflow protocol calls. Tool definitions are deployment configuration; adding/removing configured tools does not require rebuilding the image.

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

Keep non-secret runtime behavior in `agent.yaml` mounted into `/config`:

```yaml
agent:
  name: support
  instruction: Answer using approved knowledge.

model:
  provider: fake
  name: fake/default

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

## Source code is not required for configuration

Configuration, workflows, mounted knowledge, persistence endpoints, and configured tools are external runtime inputs. End users should change these inputs around the published GHCR image rather than editing or rebuilding the Open Workflow Agent source tree.

Source checkout/build instructions are intentionally kept in the [developer guide](development.md).
