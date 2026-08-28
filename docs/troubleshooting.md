# Troubleshooting, Upgrades, and Compatibility

## FAQ and troubleshooting

### `/health/ready` returns 503 (`not_ready`)

Readiness flips to `ok` only after startup initialization completes. Common causes:

- **Knowledge reload with `mode: startup`** is still running (large mounted knowledge directories take time to embed). Wait for indexing to finish, or set `knowledge.reload.mode: manual` and trigger `POST /v1/admin/knowledge/reload`.
- **External catalog resolution** is configured and still fetching/verifying pinned definitions. Readiness is intentionally withheld until every configured catalog function is fetched and pin-verified; a pin mismatch or unreachable endpoint blocks readiness (see below).
- **Persistence is unreachable.** If `persistence.datasource` points at PostgreSQL and the database is not reachable, startup fails; check `persistence.datasource` and database connectivity.
- The process crashed before readiness; check container logs (`docker logs`, `kubectl logs`).

### Configuration validation errors at startup

`invalid runtime configuration` reports pydantic validation details; `configuration root must be an object` means the YAML root is not a mapping. Unknown keys are rejected (fail-closed). Remember the precedence `built-in defaults < YAML < OWA__* environment variables` and that `OWA_CONFIG_FILE` selects the YAML file (default `/config/agent.yaml` in images).

### Knowledge reload returns unexpected counts

`GET/POST` reload reports `added/updated/deleted/unchanged`. Files whose content hash **and** embedding identity are unchanged are skipped. A changed embedding model/identity or chunking configuration re-indexes everything — the manifest records the parser, chunking identity, embedding identity, and index timestamp per file for auditing.

### External catalog pin mismatch or revalidation failure

Catalog references with integrity pins must match the configured SHA-256 digest exactly; revalidation must succeed within the configured TTL. On failure the runtime refuses the definition and withholds readiness — there is no fallback to stale or unverified content. Verify the pinned digest matches the upstream definition version and that egress to the allowlisted host is permitted.

### PostgreSQL connectivity

- Use `postgresql://user:password@host:5432/db` in `persistence.datasource` (engine extras `postgres` provide the driver; the published images include it).
- In Compose, the datasource is injected as `postgresql://...@postgres:5432/...`; the service must be healthy first (`docker compose ps`).
- Common stores live in an isolated schema namespace; ADK/LangGraph checkpoints use their own namespaces. Resetting state means deleting the namespace/tables — not just the container.

### Approvals API returns authorization errors

Approval endpoints require both the configured `approvals.operator_token` (as `Authorization: Bearer ...`) and an `X-Operator-Id` header. `approval operator authorization is not configured` means the deployment has `approvals.enabled: true` but no `operator_token`.

### Executable tasks are rejected

`run.shell`/`run.script`/`run.container` fail unless the deployment enables `sandbox.enabled` with the right backend/profile. Workflow definitions cannot enable them. Check `GET /v1/capabilities` → `features.sandbox`.

## Upgrade notes

- The runtime state directory (`/data`) holds SQLite stores, knowledge indexes, and memory. Back it up across upgrades; do not delete it between patch releases.
- Engine checkpoints are engine-native: do not switch an existing persistent invocation between ADK and LangGraph images.
- Controller images and runtime images share a private socket/API protocol per release — upgrade the sandbox controller to the same tag as the runtime and restart both together.
- Configuration is strict: new keys introduced in an upgrade are additive; remove removed keys before upgrading.

## Version and compatibility matrix

| Component | Status |
| --- | --- |
| Open Workflow DSL | `1.0.3` (only public DSL; schema is bundled, not modified) |
| Python | `3.12` (floor for every package; CI, mypy, and images target 3.12) |
| Runtime images | `bzohdy/open-workflow-agent-{adk,langgraph}` / `ghcr.io/bassemzohdy/open-workflow-agent-{adk,langgraph}` |
| Model adapter | LiteLLM (bundled in images; deterministic `fake` provider for tests) |
| Knowledge embeddings | FastEmbed 0.8.0 / ONNX, `sentence-transformers/all-MiniLM-L6-v2` (384-dim) |
| Persistence | SQLite (reference) and PostgreSQL via locked `postgres` extras |
| Engines | ADK and LangGraph (production-accepted); Microsoft Agent Framework adapter (optional, not a release target) |
| Sandbox backends | internal (accepted), Docker (accepted), Kubernetes/OpenShift (pending real-cluster acceptance) |
| Lifecycle streaming | bounded lifecycle SSE (`features.lifecycleStreaming`); not general output streaming |
