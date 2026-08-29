# Configuration Reference

Open Workflow Agent uses strict YAML configuration with `OWA__...` environment-variable overrides. Unknown properties are rejected. Runtime configuration is external to the published image, so changing models, workflows, knowledge, persistence, tools, protocol endpoints, or deployment policy does not require rebuilding the image.

Configuration precedence is:

```text
built-in defaults < YAML file < environment variables
```

The default YAML path is `/config/agent.yaml`; override it with `OWA_CONFIG_FILE`.

## Environment overrides

Nested YAML fields map to `OWA__A__B` variables:

```yaml
server:
  port: 8080
model:
  name: fake/default
```

```bash
OWA__SERVER__PORT=8080
OWA__MODEL__NAME=fake/default
```

Environment values are parsed as YAML scalars/objects. YAML values themselves are not environment templates; `${NAME}` is not expanded automatically.

## Current runtime configuration

The currently implemented runtime accepts these main blocks:

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

### Pre-release security migration note

The old single A2A bearer field (`a2a.auth_token`) has been removed. OWA has no backward-compatibility commitment before the product contract stabilizes, so it was replaced rather than preserved as an alias once the shared security-profile implementation landed.

The target schema is defined in [protocol-security-decisions.md](protocol-security-decisions.md) and tracked by `SECURITY-1` through `SECURITY-4` in `TODO.md`. Profile definitions (`security.profiles`, `type`, `token`) and A2A bearer authentication (`a2a.security_profile`) are implemented and strict-parsed today. The per-profile `authorization` block below (roles/scopes/permissions enforcement) remains conceptual — it is not yet parsed or enforced (`SECURITY-4`).

## Shared security profiles

Authentication and authorization are deployment/runtime configuration. The shared schema intentionally supports only the most common interoperable mechanisms:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Implemented today — profile definition and A2A bearer authentication:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        from_env: A2A_PARTNER_TOKEN

a2a:
  security_profile: partner-agent
```

`a2a.security_profile` must reference a `bearer`-type profile; the runtime rejects the configuration at startup otherwise (unknown profile name, or a non-bearer profile type).

Conceptual (not yet parsed or enforced) — per-profile authorization:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        from_env: A2A_PARTNER_TOKEN
      authorization:
        principal: partner-agent
        roles: [partner]
        scopes: [agent.invoke]
        permissions:
          - action: message.send
            resources: [skill:residence-renewal]
          - action: tasks.get
            resources: [skill:residence-renewal]
        audience: open-workflow-agent
```

The standard authorization vocabulary (for the conceptual block above) is:

- `principal` / identity — authenticated caller, service, or agent;
- `role` — named grouping of permissions;
- `scope` — credential/delegation authority, especially OAuth2;
- `permission` / `action` — concrete allowed operation;
- `resource` — target skill, workflow, API, or object;
- `audience` — intended token/service recipient.

Roles and scopes are intentionally distinct concepts.

Secrets must be referenced from deployment-owned secret/environment mechanisms and must never be serialized into workflows, plans, capabilities, Agent Cards, lifecycle events, A2A Tasks/artifacts, logs, or persisted invocation metadata.

Enterprise federation, token exchange, delegated-user identity, and consent remain identity-platform concerns and are deferred until a concrete requirement exists. OWA must not become an identity provider.

## Traffic policy — separate concern

Traffic management is deliberately separate from security profiles. The target deployment model uses a distinct `traffic_policy` block for rate, concurrency, burst, and related admission controls.

Conceptually:

```yaml
traffic_policy:
  inbound:
    max_concurrent_requests: 100
    requests_per_second: 50
    burst: 100
```

A security profile answers who the caller is and what it may do; traffic policy answers how much traffic may be admitted.

## `model`

For deterministic validation without a provider API:

```yaml
model:
  provider: fake
  name: fake/default
```

For real providers use the bundled LiteLLM adapter:

```yaml
model:
  provider: litellm
  name: openai/<model-name>
```

Common provider credentials remain provider-owned environment variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `OPENROUTER_API_KEY`. Ollama normally uses `model.options.api_base`.

Prefer deployment secrets instead of storing `api_key` directly in committed YAML.

## `workflow`

A workflow can be provided by path or inline definition. If neither is supplied, OWA generates the default one-task agent workflow.

```yaml
workflow:
  path: /config/workflow.yaml
```

External function catalogs are opt-in and deployment-trusted. Workflows may reference approved aliases, but cannot carry credentials, relax TLS/redirect/allowlist policy, execute untrusted remote scripts, or replace built-in `agent`/`llm` functions.

Supported declarative remote protocol adapters are bounded `http`, `mcp`, `a2a`, and `openapi` calls.

## `knowledge`, `embedding`, and `memory`

Mounted knowledge is indexed by the common knowledge service and exposed through `search_knowledge`. The standard images package the FastEmbed/ONNX `all-MiniLM-L6-v2` model.

Memory supports `false`, `true`, or `auto` and exposes `add_memory`, `search_memory`, and `delete_memory`. Memory is separate from engine checkpoint/resume state.

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

Prefer injecting production datasources via `OWA__PERSISTENCE__DATASOURCE` from a deployment secret. Subsystems use isolated namespaces/tables; ADK and LangGraph keep engine-native checkpoint formats separate.

## `approvals`

Approvals are disabled by default. The current `approvals.operator_token` is a bounded pre-security-profile field and is expected to migrate to the shared security model rather than become a permanent parallel authentication mechanism.

## `a2a`

The implemented inbound A2A boundary targets stable A2A release `1.0.1` and advertises protocol version `1.0`.

```yaml
a2a:
  enabled: true
  transport: jsonrpc   # jsonrpc | http_json
  path: /a2a
  public_base_url: https://agents.example.com
```

Discovery is:

```text
GET /.well-known/agent-card.json
```

JSON-RPC uses `SendMessage`; HTTP+JSON uses `/a2a/message:send`. Legacy v0.3 discovery paths, `message/send`, and legacy Part forms are intentionally not retained.

Persistent A2A Tasks, Task get/cancel, multi-skill routing, async Task behavior, and streaming/resubscription remain backlog items and must not be advertised as implemented until their gates are green.

## `sandbox`

Executable workflow operations are disabled by default. All engines route execution through the common `SandboxManager`.

Backends:

- `internal` — controlled child-process execution, not hard isolation;
- `docker` — restricted external controller boundary;
- `kubernetes` — Kubernetes/OpenShift controller boundary with deployment-owned policy.

The backend request/result/interface contract remains in `sandbox/contract.py`; portable requirements/capability compatibility live in `sandbox_capabilities.py`. These are intentionally separate concepts.

## `tools`

Configured tool types are:

```text
mcp
openapi
a2a
```

Protocol clients enforce bounded HTTP behavior such as TLS verification, timeouts, redirect policy, response-size limits, endpoint validation, and host allowlists.

The common client is pinned to MCP `2026-07-28` and A2A `1.0` wire semantics. OpenAPI support remains a bounded operation adapter and must not be described as full OpenAPI 3.2 document conformance until that implementation and conformance coverage exist.

## Observability

Lifecycle records use CloudEvents `1.0` structured JSON semantics and expose common identifiers plus sanitized error information. Engine-native checkpoint state and secrets are never part of the public lifecycle contract.

## Recommended deployment pattern

Keep non-secret behavior in mounted YAML and inject sensitive/environment-specific values separately:

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

```bash
OPENAI_API_KEY=replace-me
OWA__PERSISTENCE__DATASOURCE='postgresql://user:password@host:5432/database'
```

Do not put API keys, passwords, bearer tokens, certificates, or private keys in workflow definitions.

See [protocol baselines](protocol-baselines.md), [protocol/security decisions](protocol-security-decisions.md), [API guide](api.md), and [deployment guide](deployment.md) for the authoritative boundaries.
