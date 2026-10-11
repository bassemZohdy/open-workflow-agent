# Security boundaries

This page describes the runtime security guards applied to workflow execution
and the HTTP server. The guards fail **closed**: untrusted input is denied by
default, and configuration must explicitly widen the surface.

## Egress (SSRF) protection

Every outbound HTTP(S) request from a workflow — `call: http`, OpenAPI, A2A,
MCP HTTP, OAuth token exchanges, and gRPC hosts — passes through the egress
guard (`openworkflow_adk.security.security.validate_egress`).

The guard:

- rejects loopback, private, link-local, reserved, and multicast addresses;
- **resolves hostnames by default** and blocks the request when any resolved
  address is disallowed (a name that resolves to `169.254.169.254` is denied);
- treats DNS failures as blocked unless `WORKFLOW_EGRESS_ALLOW_UNRESOLVED=1`;
- re-validates **every redirect hop** through per-request httpx hooks, closing
  the resolve-then-connect rebinding window;
- is bypassed for exact hosts on `WORKFLOW_EGRESS_ALLOWLIST` (intended for
  trusted internal services).

Legacy pass-through for non-IP hostnames is available only via
`WORKFLOW_EGRESS_SKIP_DNS=1`; never enable it on an untrusted boundary.

`WORKFLOW_AIRGAPPED=1` blocks all network egress.

## Container isolation

`run: container` tasks are hardened by default:

- **Host volumes are denied** unless every host path is under a root listed in
  `WORKFLOW_CONTAINER_VOLUME_ALLOWLIST`. Mounts default to read-only (`ro`).
- The container runs with `network_mode: none` unless a network is explicitly
  requested **and** appears on `WORKFLOW_CONTAINER_NETWORK_ALLOWLIST`.
- Host port publishing is disabled unless `WORKFLOW_CONTAINER_PORTS_ALLOWED=1`.
- Hard resource caps are applied when configured:
  `WORKFLOW_CONTAINER_CPU_LIMIT`, `WORKFLOW_CONTAINER_MEMORY_LIMIT`,
  `WORKFLOW_CONTAINER_PIDS_LIMIT`.

## Exec configuration is static

Workflow state can hold attacker-controlled content (HTTP responses, tool
output, LLM output written via `output_key`). Binding such state into an
exec-family configuration would turn prompt injection into code execution.
The loader therefore **rejects expression-bound values** (`${...}`) in
`run.shell.command`, `run.script.code`, `run.container.image`/`command`,
`run.container.arguments`/`environment`, and MCP stdio `command` at translate
time.

## MCP stdio servers

MCP stdio transports execute a document-controlled command, so the command
must appear on `WORKFLOW_MCP_COMMAND_ALLOWLIST` (comma-separated names or
paths). The process is killed if it does not exit within
`WORKFLOW_MCP_TIMEOUT_SECONDS` (default 60). `WORKFLOW_MCP_ALLOW_UNLISTED=1`
disables the check; not recommended.

## Local resource reads

`call: openapi`/`asyncapi`/`a2a`/`grpc` resource documents are read from a
base directory — the working directory or `WORKFLOW_RESOURCE_BASE_DIR` — and
paths that escape it are rejected, mirroring the script-source confinement of
`run: script` (`WORKFLOW_SCRIPT_BASE_DIR`). Remote resources are egress
checked, and `proto` resources may be pinned with a `sha256` digest.

## Subprocess execution

`run: shell` and `run: script` tasks execute child processes with a
60-second default timeout (`WORKFLOW_RUN_DEFAULT_TIMEOUT`), resource limits
when configured, and a sanitized environment. Variables beginning with
`WORKFLOW_SECRET__` are never passed to those child processes.

The process sandbox is best-effort. It applies POSIX resource limits and Linux
`no-new-privileges`, but it does not create a network namespace or install a
seccomp profile. Sandboxed code can therefore still make outbound network
connections; use container or deployment-level network policy for isolation.

## Expression safety

JSONata evaluation is bounded by `WORKFLOW_EXPRESSION_MAX_LENGTH` (10000),
`WORKFLOW_EXPRESSION_MAX_DEPTH` (100), and a wall-clock budget
(`WORKFLOW_EXPRESSION_TIMEOUT_SECONDS`, default 0.25) that interrupts
pathological expressions on POSIX via `SIGALRM` and on other platforms via an
async-exception timer. Dynamic function forms (`$eval`, `$function`) are
disabled.

## SAML metadata parsing

SAML metadata is parsed with `defusedxml.ElementTree`, which rejects entity
expansion and other XXE payloads.

## HTTP server

The FastAPI server (`owf-adk serve`, `owf-adk dashboard`) exposes `/run`,
`/run/stream`, `/openapi.json`, and `/metrics`. The `/health` endpoint is
public; all other endpoints require authentication when credentials are
configured:

- Static API keys from `WORKFLOW_SERVER_API_KEY` (comma-separated) or an
  explicit `ServerAuthConfig(api_keys=...)`, accepted via
  `Authorization: Bearer <key>` or `X-API-Key`.
- An optional `token_verifier` callable that maps an OIDC/opaque token to a
  `Principal` (the server never verifies tokens itself), plus an optional
  `AccessPolicy` for authorization.

The caller-supplied `user_id` field is ignored; identity is derived from the
authenticated credential. **Binding to any non-loopback host requires
authentication** — `serve()` raises if `--host` is not 127.0.0.1 and no
credentials are configured.

Internal exceptions are never returned to clients. Failures produce a generic
`{"code": "internal_error", "correlation_id": ...}` response; the detail is
logged server-side through the redaction path.

## Secret redaction

Known secrets (`use.secrets` resolved through `WORKFLOW_SECRET__<name>`) are
redacted from checkpointed state, persisted event logs, and run-logger output.
Resumed runs consume redacted state so secrets stay out of resumable storage
by reference rather than by value.
