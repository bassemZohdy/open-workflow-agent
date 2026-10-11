# 0010 — Fail-closed security posture (C24 hardening)

- Status: accepted
- Date: 2026-08-11
- Applies to: v0.2.1+

## Context

The v0.2.0 tree was reviewed across code correctness, security, and
architecture. The security findings shared a theme: the runtime permitted
document- or state-controlled behavior unless a guard was explicitly
configured. That is the wrong default for a translator that can execute shell
commands, spawn containers and MCP servers, make arbitrary network calls, and
expose an HTTP execution endpoint.

## Decision

Flip the default posture to **fail closed** across the execution surface, and
make widening explicit and auditable:

1. **Egress (SSRF)** — `validate_egress` resolves hostnames by default and
   blocks when any resolved address is private/loopback/link-local; DNS
   failures are blocked; every redirect hop is re-validated through guarded
   httpx clients; exact hosts may be allowlisted.
2. **Containers** — volume mounts are denied without an allowlist, the default
   network mode is `none`, host port publishing is off, and resource caps are
   applied from environment defaults.
3. **Exec configs are static** — expression-bound `run.*`/MCP `command` values
   are rejected at translate time, closing the prompt-injection →
   code-execution chain.
4. **MCP stdio** — server commands are allowlisted and killed after a timeout.
5. **HTTP server** — authentication (API key and/or a caller-provided OIDC
   token verifier) gates every non-health endpoint; non-loopback binds require
   credentials; internal exception text is replaced with correlation IDs.
6. **Persistence** — secrets are redacted from checkpoints, event logs, and
   logger output; resumed runs consume redacted state.

Correctness and architecture work from the same review (atomic event appends,
off-loop blocking I/O, nested resume, `RunConfig`, import-linter layering,
`adk_compat` seam, extras split, integration-test markers, API surface trim)
is recorded in [TODO.md](../TODO.md) under C24.

## Consequences

- Default configurations are safer at the cost of requiring explicit
  allowlists for legitimate container volumes, MCP servers, and non-loopback
  serving. The documented escape hatches (`WORKFLOW_EGRESS_SKIP_DNS`,
  `WORKFLOW_MCP_ALLOW_UNLISTED`, …) are retained for controlled deployments
  and test doubles.
- The stable public surface (`__all__`) now covers only load/run/translate/
  history primitives; enterprise adapters (OIDC/SAML, RBAC/audit) are
  provisional and explicitly unverified until verification is implemented.
