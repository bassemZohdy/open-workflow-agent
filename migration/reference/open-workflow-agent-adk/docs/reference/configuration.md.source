# Configuration reference

Agent values use three layers, with later entries overriding earlier ones:

```text
document.metadata.adk.agent_defaults < task.metadata.adk.agent < environment
```

Project-wide agent defaults live in `document.metadata.adk.agent_defaults`:

```yaml
document:
  metadata:
    adk:
      agent_defaults:
        model: gemini-2.5-flash
        instruction: Default agent instruction.
```

Environment variables use the `WORKFLOW_` prefix and `__` for nesting:

```text
WORKFLOW_AGENT__MODEL=gemini-2.5-flash
WORKFLOW_AGENT__GENERATE_CONTENT_CONFIG__temperature=0.2
```

Boolean, numeric, null, and JSON object/array values are decoded when possible.
Defaults may be supplied as a mapping or YAML/JSON file to
`resolve_agent_characteristics`.

The CLI supports dotenv-style files with `--env`; values are loaded into the
process environment before the document is translated.

Named model providers live in `document.metadata.adk.providers`:

```yaml
document:
  metadata:
    adk:
      providers:
        openai-prod:
          type: openai
          base_url: https://api.openai.com/v1
          credential: openai-key
use:
  secrets: [openai-key]
```

The corresponding deployment overrides are
`WORKFLOW_PROVIDERS__OPENAI-PROD__BASE_URL` and
`WORKFLOW_PROVIDERS__OPENAI-PROD__CREDENTIAL`. Credentials resolve from
`WORKFLOW_SECRET__<name>` and are never taken from inline provider values.
OpenAI-compatible adapters currently cover OpenAI, Azure, Ollama, and vLLM;
Gemini and Anthropic continue through ADK's native model resolution.

Semantic memory uses the same named-registry pattern under
`document.metadata.adk.memories`:

```yaml
document:
  metadata:
    adk:
      memories:
        local:
          type: file
          connection: .workflow-memory.json
```

`agent.memory: {use: local}` selects the ADK memory service for the run. The
available backends are `in-memory`, `file`, `redis`, `postgres`, and `vertex`.
Redis/Postgres round-trip tests run through the opt-in Testcontainers suite;
Vertex additionally requires ADK's GCP extra and an agent engine ID.
File, Redis, Postgres, and Vertex services can be reused across run instances;
completed ADK sessions are added to the configured memory service after a run,
and the agent's `load_memory` tool can recall matching prior context.

Multimodal agent input can be passed through with
`run_workflow(..., message=google.genai.types.Content(...))`; image and audio
parts are forwarded unchanged to ADK.

Callers that need incremental delivery can provide `event_sink`; it is invoked
for every ADK event as it arrives and may be synchronous or asynchronous. The
existing return value remains the complete event list.

Qualified catalog calls use the OpenWorkflow form
`function:version@catalog`. Define the catalog root under `use.catalogs`; the
runtime resolves `functions/<function>/<version>/function.yaml` before graph
construction. Repository roots from GitHub and GitLab are mapped to their raw
machine-readable paths. For local catalogs, pass `catalog_base_dir` to
`run_workflow` (the CLI also provides `--catalog-base-dir`); local resources are
confined to that base.

Set `token_budget` on `run_workflow` to enforce a run-level usage ceiling from
ADK `total_token_count` metadata. Agent tasks continue to select models and
providers independently through their `metadata.adk.agent.model`/`provider` configuration.
Session/state persistence is a separate concern and uses ADK's `InMemory`,
database, or Vertex session services configured through
`WORKFLOW_SESSION_BACKEND`.

Runtime sessions use `InMemorySessionService` by default. Set
`WORKFLOW_SESSION_BACKEND=vertex` for `VertexAiSessionService`, with
`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and optionally
`WORKFLOW_VERTEX_AGENT_ENGINE_ID` supplied by the deployment environment.
For a self-hosted durable backend, use `WORKFLOW_SESSION_BACKEND=sqlite` and
set `WORKFLOW_SESSION_DATABASE_URL` to a SQLAlchemy async database URL (for
example, `sqlite+aiosqlite:///workflow-sessions.db`).

Run-history checkpoints default to every emitted event. Set
`WORKFLOW_CHECKPOINT_INTERVAL=0` to disable intermediate writes, or pass
`checkpoint_interval` to `run_workflow`; the selected history backend remains
the checkpoint backend.

For a process restart, call `run_workflow(..., resume=True)` with the same
SQLite history and session identifiers. The runtime resumes after the last
successfully checkpointed top-level task and reuses its persisted state.

When a persistent history backend is supplied, timer waits can be suspended
instead of keeping a task alive. `run_workflow` records `status: suspended`,
the wait task, and `resume_at`; a later `resume=True` call continues after the
timer. The threshold defaults to one hour and can be changed with
`WORKFLOW_SUSPEND_WAIT_SECONDS` or the `suspend_after` argument.
With persistent history, broker-backed `listen` tasks also suspend without
holding a consumer task. The matching event is consumed when the run is
resumed, so the broker must retain it between suspension and resume.

Agent tasks can request external approval/input with
`metadata.adk.agent.request_input: {question: ...}`. The runtime records a `human_input`
suspension and resumes it with `resume=True` plus `resume_input=<value>`.

## Security-related environment variables

The egress guard, container hardening, and server authentication introduced
under C24 are configured through the environment. See
[`security.md`](security.md) for the full security reference.

| Variable | Default | Effect |
|---|---|---|
| `WORKFLOW_EGRESS_ALLOWLIST` | *(none)* | Comma-separated exact hosts that bypass the egress address check |
| `WORKFLOW_EGRESS_ALLOW_UNRESOLVED` | *(off)* | `1` allows hostnames that fail DNS resolution |
| `WORKFLOW_EGRESS_SKIP_DNS` | *(off)* | `1` restores legacy pass-through for non-IP hostnames (test doubles only) |
| `WORKFLOW_AIRGAPPED` | *(off)* | `1` blocks all network egress |
| `WORKFLOW_CONTAINER_VOLUME_ALLOWLIST` | *(deny all)* | Comma-separated host roots that may be mounted into containers |
| `WORKFLOW_CONTAINER_NETWORK_ALLOWLIST` | *(none only)* | Comma-separated network modes a container may request |
| `WORKFLOW_CONTAINER_PORTS_ALLOWED` | *(off)* | `1` enables container host port publishing |
| `WORKFLOW_CONTAINER_CPU_LIMIT` / `_MEMORY_LIMIT` / `_PIDS_LIMIT` | *(unset)* | Hard container resource caps |
| `WORKFLOW_MCP_COMMAND_ALLOWLIST` | *(deny all)* | Comma-separated MCP stdio server commands |
| `WORKFLOW_MCP_ALLOW_UNLISTED` | *(off)* | `1` disables the MCP command allowlist |
| `WORKFLOW_MCP_TIMEOUT_SECONDS` | `60` | Kill timeout for MCP stdio servers |
| `WORKFLOW_CONSUME_TIMEOUT_SECONDS` | `3600` | Bound on AsyncAPI consumer waits (task timeout wins when set) |
| `WORKFLOW_RESOURCE_BASE_DIR` | cwd | Base directory for local resource reads in `call` tasks |
| `WORKFLOW_SERVER_API_KEY` | *(none)* | Comma-separated API keys for the HTTP server (required for non-loopback binds) |
| `WORKFLOW_EXPRESSION_TIMEOUT_SECONDS` | `0.25` | Wall-clock budget for JSONata evaluation |
| `WORKFLOW_EXPRESSION_MAX_LENGTH` | `10000` | Maximum expression length |
| `WORKFLOW_EXPRESSION_MAX_DEPTH` | `100` | Maximum expression nesting depth |
