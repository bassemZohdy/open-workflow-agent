# Configuration Reference

Open Workflow Agent uses strict YAML configuration with environment-variable overrides. For normal use, configuration is mounted into a published container image; changing configuration, workflows, knowledge, memory, persistence, tools, or model providers does not require rebuilding the image.

Docker Hub is the default registry in end-user examples:

```text
bzohdy/open-workflow-agent-adk:<tag>
bzohdy/open-workflow-agent-langgraph:<tag>
```

The same verified builds and tags are also published to the canonical GitHub Container Registry (GHCR):

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk:<tag>
ghcr.io/bassemzohdy/open-workflow-agent-langgraph:<tag>
```

Published images use:

```text
/config     runtime configuration and workflow definitions
/knowledge  mounted knowledge documents
/data       writable runtime state
```

Example image:

```text
bzohdy/open-workflow-agent-adk:latest
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
  bzohdy/open-workflow-agent-adk:latest
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
  name: provider/model
  temperature: 0.0
  options: {}

workflow:
  path: null
  definition: null
  catalog: []
  external_catalogs: {}

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

approvals:
  enabled: false
  operator_token: null

a2a:
  enabled: false
  transport: jsonrpc
  path: /a2a
  agent_name: Open Workflow Agent
  agent_description: Configuration-driven Open Workflow runtime over A2A.
  agent_version: 0.1.0
  public_base_url: null
  auth_token: null
  max_message_chars: 100000

sandbox:
  enabled: false
  backend: internal
  allow_shell: false
  script_runtimes: [python]
  timeout_seconds: 30.0
  max_input_bytes: 1048576
  max_output_bytes: 1048576
  max_workspace_bytes: 33554432
  workspace_root: /tmp/owa-sandbox
  executable_search_path: /opt/venv/bin:/usr/local/bin:/usr/bin:/bin
  inherited_environment: []
  secret_environment: []
  cpu_seconds: 30
  memory_bytes: 536870912
  file_size_bytes: 33554432
  process_count: 64
  docker:
    controller_socket: /run/owa-sandbox/controller.sock
    allowed_images: []
    require_digest: true
    run_as_user: 65532:65532
    network: denied
  kubernetes:
    controller_url: http://127.0.0.1:8090
    allowed_images: []
    require_digest: true
    platform: kubernetes
    network: denied
    network_policy_enforced: false
    process_limit_enforced: false
    secret_name: null
    secret_keys: []

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

For deterministic validation without a provider API:

```yaml
model:
  provider: fake
  name: fake/default
  temperature: 0.0
  options: {}
```

For real providers use the bundled LiteLLM adapter:

```yaml
model:
  provider: litellm
  name: provider/model
  temperature: 0.1
```

Runtime behavior:

- `provider: fake` uses the deterministic built-in model for tests and runtime validation.
- `provider: litellm` is the recommended value for real providers.
- LiteLLM chooses the actual upstream provider from the prefix in `model.name`.
- the standard ADK and LangGraph release images bundle LiteLLM, so changing providers does not require rebuilding the image.

### Common LiteLLM providers

| Provider | `model.name` format | Credential / connection |
| --- | --- | --- |
| OpenAI | `openai/<model>` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/<model>` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/<provider>/<model>` | `OPENROUTER_API_KEY` |
| Ollama | `ollama/<local-model>` | no API key normally; set `model.options.api_base` |
| Other LiteLLM provider | `<provider-prefix>/<model>` | provider-specific environment variables or `model.options` |

The model identifiers above intentionally use placeholders because available provider models change independently of this runtime. Use the provider/model identifier documented by LiteLLM and the upstream provider.

### OpenAI

`agent.yaml`:

```yaml
model:
  provider: litellm
  name: openai/<model-name>
```

Credential:

```bash
OPENAI_API_KEY=replace-me
```

With the repository Compose helper, put both selections in `.env`:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=openai/<model-name>
OPENAI_API_KEY=replace-me
```

For a published image started directly with Docker:

```bash
docker run --rm \
  -p 8080:8080 \
  -e OWA__MODEL__PROVIDER=litellm \
  -e 'OWA__MODEL__NAME=openai/<model-name>' \
  -e OPENAI_API_KEY \
  bzohdy/open-workflow-agent-adk:latest
```

### Anthropic

`agent.yaml`:

```yaml
model:
  provider: litellm
  name: anthropic/<model-name>
```

Credential:

```bash
ANTHROPIC_API_KEY=replace-me
```

Compose `.env`:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=anthropic/<model-name>
ANTHROPIC_API_KEY=replace-me
```

### OpenRouter

OpenRouter model IDs contain the upstream provider/model path after the LiteLLM `openrouter/` prefix:

```yaml
model:
  provider: litellm
  name: openrouter/<provider>/<model-name>
```

Credential:

```bash
OPENROUTER_API_KEY=replace-me
```

Compose `.env`:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=openrouter/<provider>/<model-name>
OPENROUTER_API_KEY=replace-me
```

### Ollama

For Ollama running outside the Open Workflow Agent container:

```yaml
model:
  provider: litellm
  name: ollama/<local-model-name>
  options:
    api_base: http://host.docker.internal:11434
```

Repository Compose `.env`:

```dotenv
MODEL_PROVIDER=litellm
MODEL_NAME=ollama/<local-model-name>
OWA__MODEL__OPTIONS__API_BASE=http://host.docker.internal:11434
```

The repository `compose.yaml` maps `host.docker.internal` to the Docker host for local development. Ollama normally requires no API key.

For Kubernetes/OpenShift, do not use `host.docker.internal`; use the DNS name or URL of the Ollama service reachable from the runtime pod, for example:

```yaml
model:
  provider: litellm
  name: ollama/<local-model-name>
  options:
    api_base: http://ollama.ai-platform.svc.cluster.local:11434
```

### Any other LiteLLM provider

Use the same pattern:

```yaml
model:
  provider: litellm
  name: <litellm-provider-prefix>/<model-name>
```

Then provide the provider-specific credential/environment variables documented by LiteLLM. The repository Compose helper passes variables from the local `.env` file through to the ADK or LangGraph runtime container, so provider-specific variables can be added there without changing the image.

For providers or OpenAI-compatible endpoints where explicit connection values are easier, the runtime passes `model.options` directly to LiteLLM:

```yaml
model:
  provider: litellm
  name: <provider-prefix>/<model-name>
  options:
    api_key: replace-me
    api_base: https://provider.example/v1
    api_version: optional-version
```

Prefer environment variables/secrets rather than storing `api_key` in a committed YAML file. The equivalent runtime environment variables are:

```bash
OWA__MODEL__OPTIONS__API_KEY=replace-me
OWA__MODEL__OPTIONS__API_BASE=https://provider.example/v1
OWA__MODEL__OPTIONS__API_VERSION=optional-version
```

Provider prefixes and provider-specific authentication requirements are maintained by LiteLLM: <https://docs.litellm.ai/>.

### `.env.example` and Compose

For repository-local development:

```bash
cp .env.example .env
```

Then edit the model section of `.env` and start one engine:

```bash
docker compose --profile adk up --build
# or
docker compose --profile langgraph up --build
```

`compose.yaml` reads the model-selection variables and also passes the local `.env` into the runtime container so LiteLLM can see provider-specific variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `OPENROUTER_API_KEY`.

`.env` is ignored by git. Populate only the credentials needed for the selected provider. Production deployments should use Kubernetes/OpenShift Secrets, Docker secrets, or an equivalent secret manager instead of a shared `.env` file.

CI does not call paid providers; it validates the runtime with `fake/default` and verifies that LiteLLM is importable in both built engine images.

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

`workflow.catalog` registers deployment-provided local child workflows for `run.workflow`.

External function catalogs are opt-in and require a deployment trust policy. The
workflow may name an approved alias, but it cannot supply credentials or relax
transport controls:

```yaml
workflow:
  path: /config/workflow.yaml
  external_catalogs:
    trusted:
      allowed_hosts:
        - catalog.example.com
        - api.example.com
      allowed_endpoints:
        - https://catalog.example.com/open-workflow
      timeout_seconds: 10
      max_response_bytes: 4000000
      follow_redirects: false
      verify_tls: true
      cache_ttl_seconds: 300
      max_cache_age_seconds: 86400
      max_cache_entries: 128
      revalidate: true
      require_integrity_pin: true
      integrity_pins:
        "echo:1.0.0@trusted": <sha256-of-function-yaml>
      authentication:
        bearer_token_env: OWA_CATALOG_TOKEN
```

The workflow references a function with its exact semantic version:

```yaml
use:
  catalogs:
    trusted:
      endpoint:
        uri: https://catalog.example.com/open-workflow
do:
  - call_echo:
      call: echo:1.0.0@trusted
```

Only declarative catalog functions whose definition calls one of the bounded
`http`, `mcp`, `a2a`, or `openapi` protocol adapters are enabled. Remote
`run.script` functions and inline workflow credentials are rejected. Catalog
fetches use HTTPS, exact host allowlists, TLS verification, no redirects,
bounded responses, conditional revalidation, and fail-closed errors. Credentials
are read from deployment environment variables and are never serialized into
workflow plans or capability responses. If external catalog configuration is
absent, `use.catalogs` remains rejected and local `workflow.catalog` continues
to work without network access.

The resolver supports versioned `function.yaml` entries with a declarative
protocol call. It does not execute catalog scripts, follow remote references,
or let external entries replace the built-in `agent` and `llm` functions. A
missing alias, malformed definition, transport failure, expired or unavailable
revalidation, or pin mismatch fails startup/readiness; there is no fallback to
unverified or stale content. The in-memory cache is bounded by entry count and
age and is separate from invocations, memory, knowledge, approvals, schedules,
and engine checkpoints. Catalog events retain only sanitized status/error codes
and correlation IDs.

Workflow files are mounted under `/config`; adding or replacing them does not require rebuilding the published image.

### Scheduling a workflow

The workflow document itself declares scheduling with exactly one of
`schedule.after` or `schedule.every` (a `duration`):

```yaml
document:
  dsl: '1.0.3'
  namespace: examples
  name: nightly-cleanup
  version: '1.0.0'
schedule:
  every:
    hours: 6
do:
  - run:
      call: agent:1.0.0@default
      with:
        input: ${ .input }
```

```yaml
schedule:
  after:
    minutes: 30
```

- `after` creates one delayed start; `every` creates recurring starts after each
  successful completion.
- Schedule metadata is durable in the runtime store; dispatch is owned by one
  runtime process and a restart reclaims an interrupted dispatch (at-least-once).
- Schedules are created through `POST /v1/schedules`, which validates the
  configured workflow's schedule block; `cron` and event-triggered `on` are part
  of Open Workflow 1.0.3's schema but are **not supported by the runtime** —
  using them fails validation (the bounded profile has no cron parser or event
  trigger).

### Sub-workflows with `run.workflow`

Register deployment-provided child workflows under `workflow.catalog`, then call
them with the standard `run` task:

```yaml
workflow:
  catalog:
    - document:
        dsl: '1.0.3'
        namespace: examples
        name: child-summary
        version: '1.0.0'
      do:
        - summarize:
            call: agent:1.0.0@default
            with:
              input: ${ .text }
```

```yaml
do:
  - summarize:
      run:
        workflow:
          namespace: examples
          name: child-summary
          version: 1.0.0
        input:
          text: ${ .input }
```

`version: latest` selects the highest registered version when omitted. The
child gets its own invocation/session identity and runs on the selected
engine's native path; lifecycle events retain `parent_invocation_id` and
`parent_task_reference`.

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

When memory is enabled, these tools are registered automatically as agent tools,
so the configured agent can remember and recall facts across invocations:

```text
add_memory    {"text": "...", "metadata": {...}}  ->  {"id": 1}
search_memory {"query": "renewal", "limit": 5}    ->  [{...}, ...]
delete_memory {"id": 1}                           ->  {"deleted": true}
```

`search_knowledge` is the knowledge counterpart (always registered when
knowledge is configured). A minimal walkthrough:

```text
1. Set memory.enabled: true (or auto with a database-backed runtime).
2. Invoke the agent: "Remember that customer 42's invoice is overdue."
   The agent calls add_memory; the fact persists in /data/memory.sqlite3.
3. In a later invocation (or after a restart), ask: "What do you remember
   about customer 42?" The agent calls search_memory and retrieves the fact.
```

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

## `approvals`

```yaml
approvals:
  enabled: true
  operator_token: set-via-deployment-secret
```

Disabled by default. When enabled, workflows can compose durable human-in-the-loop approvals from standard `emit`/`listen` tasks, and the operator endpoints under `/v1/approvals` become active. The `operator_token` is the bearer credential for operator decisions and inbox reads; supply it through a deployment secret rather than a plain file. Decisions additionally require an `X-Operator-Id` header and are idempotent per `Idempotency-Key`. See [api.md](api.md#approvals-human-in-the-loop) for the operator flow.

## `a2a`

```yaml
a2a:
  enabled: true
  transport: jsonrpc   # jsonrpc (default) | http_json
  auth_token: set-via-deployment-secret
```

Disabled by default. When enabled, the runtime exposes a bounded inbound A2A
profile: an Agent Card (`GET /a2a/agent.json`, also `/.well-known/agent.json`)
and a synchronous `message/send` endpoint with two selectable transport
implementations (`jsonrpc` — the most deployed — and `http_json`). Bearer
`auth_token` is optional but recommended for any non-loopback exposure. The
bounded profile has no streaming, push notifications, or task objects. See
[api.md](api.md#inbound-a2a-optional-bounded).

`public_base_url` sets the externally reachable address published in the
Agent Card's `url` field. Set it whenever the runtime sits behind a reverse
proxy or TLS terminator; without it the card derives the URL from the
incoming request, which is only correct for direct exposure. `agent_version`
defaults to the runtime release version. See
[api.md](api.md#inbound-a2a-optional-bounded).

## `sandbox`

```yaml
sandbox:
  enabled: true
  backend: internal   # internal | docker | kubernetes
  allow_shell: false
  script_runtimes: [python]
  timeout_seconds: 30.0
```

Disabled by default; workflow definitions cannot enable execution on their own. `run.script`/`run.shell` use the internal backend (`allow_shell: true` additionally enables shell execution), and `run.container` requires the `docker` or `kubernetes` backend with its controller configuration (see the `docker:`/`kubernetes:` blocks in the full shape above and [deployment.md](deployment.md)). Process controls (`cpu_seconds`, `memory_bytes`, `file_size_bytes`, `process_count`, output/input bounds, dedicated workspace, environment filtering with `inherited_environment`/`secret_environment`) apply to the internal backend. The internal sandbox is a controlled execution boundary — not container/VM isolation. `/v1/capabilities` reports the effective `features.sandbox` block for the selected backend.

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

### Lifecycle CloudEvents reference

Lifecycle records are exposed as bounded CloudEvents 1.0 structured JSON
snapshots via `GET /v1/events/lifecycle` (source
`urn:open-workflow-agent:lifecycle`) and via the bounded SSE stream. Event
`type` values include the task/workflow lifecycle (`TaskStarted`,
`TaskCompleted`, `TaskFaulted`, `TaskCancelled`, `TaskProgress`, `TaskWaiting`,
`TaskRetried`, `WorkflowStarted`, `WorkflowCompleted`, `WorkflowFaulted`,
`WorkflowCancelled`, `WorkflowResumed`, `WorkflowWaiting`) and the approval
lifecycle:

```text
io.openworkflow.agent.approval.requested   approval request persisted (emit of this type creates the record)
io.openworkflow.agent.approval.decided     terminal decision recorded via the approval API
```

Events expose only common identifiers and sanitized error codes — never
engine-native checkpoint state or secrets. Generic workflow `emit` events are
separate from lifecycle events and remain process-local.

## Recommended deployment pattern

Keep non-secret runtime behavior in `agent.yaml` mounted into `/config`:

```yaml
agent:
  name: support
  instruction: Answer using approved knowledge.

model:
  provider: litellm
  name: openai/<model-name>

workflow:
  path: /config/workflow.yaml

knowledge:
  path: /knowledge
```

Inject secrets and environment-specific values separately using the variable names expected by the provider plus runtime overrides such as:

```bash
OPENAI_API_KEY=replace-me
OWA__PERSISTENCE__DATASOURCE='postgresql://user:password@host:5432/database'
```

Do not place API keys, passwords, or bearer tokens directly in ordinary workflow definitions.

## Source code is not required for configuration

Configuration, model selection, workflows, mounted knowledge, persistence endpoints, and configured tools are external runtime inputs. End users should change these inputs around the published Docker Hub or GHCR image rather than editing or rebuilding the Open Workflow Agent source tree.

Source checkout/build instructions are intentionally kept in the [developer guide](development.md).
