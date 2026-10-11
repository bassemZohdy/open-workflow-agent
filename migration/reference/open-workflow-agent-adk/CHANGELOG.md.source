# Changelog

## Unreleased — 2026-08-17 cleanup

- **Security:** reject expression-bound `run.container.arguments` and
  `run.container.environment` values, and scope unresolved-host test bypasses to
  mocked resource tests.
- **Correctness:** compare workflow versions with PEP 440 ordering, preserve raw
  fork branches during resume, accept both `detail` and `details` error filters,
  and make retry/self-heal composition explicit and tested.
- **Robustness:** sanitize generated Temporal identifiers, hide internal LSP
  errors from clients, deduplicate duplicate-task diagnostics, and expose the
  benchmark shim's `main()` entrypoint.
- **Packaging and CI:** add the optional `temporal` dependency group, restore
  `contents: read` for release publishing, re-export `CatalogConfig`, and align
  the expression-timeout example with the runtime default.
- **Spec parity:** resolve qualified catalog function calls, support nested
  `listen.to.until` strategies, and implement `listen.read: raw`.

## 0.2.1 — 2026-08-11

C24 post-review hardening: security, correctness, and architecture work from the
v0.2.0 review.

### Security

- **HTTP server authentication** (`C24.1`): `/run`, `/run/stream`,
  `/openapi.json`, and `/metrics` require credentials when `WORKFLOW_SERVER_API_KEY`
  or an explicit `ServerAuthConfig` is configured; `user_id` is derived from the
  credential, not the request body; binding to a non-loopback host requires auth.
- **Fail-closed egress guard** (`C24.2`): hostnames are resolved and blocked by
  default (SSRF via DNS names is denied), every redirect hop is re-validated
  through guarded httpx clients, and OAuth/gRPC targets are checked.
- **Container hardening** (`C24.3`): deny-all volume mounts, `network_mode: none`
  by default, host port publishing disabled, hard CPU/memory/pids caps.
- **Static exec configs** (`C24.4`): expression-bound `run.*` and MCP `command`
  values are rejected at translate time (prompt-injection → code-execution chain).
- **MCP stdio allowlist + kill timeout** (`C24.5`), **confined local resource
  reads** (`C24.6`), **gRPC egress/TLS/proto pinning** (`C24.7`), **defusedxml
  SAML parsing** (`C24.8`), **generic errors with correlation IDs** (`C24.9`),
  and **redacted persisted event logs** (`C24.10`).

### Correctness

- `PostgresRunHistory.record_event` appends atomically via JSONB `||` (`C24.11`).
- Blocking history/container I/O moved off the event loop (`C24.12`).
- Resume works for checkpoints nested in `do`/`try`/`fork`/`switch` (`C24.13`).
- gRPC `sys.path` cleanup race fixed (`C24.14`); history dispatch is
  method-allowlisted (`C24.15`); AsyncAPI consumer waits are bounded (`C24.16`);
  the expression time budget now actually interrupts on POSIX (`C24.17`).

### Architecture

- Layering enforced with import-linter in CI: `WorkflowRegistry` moved to core,
  `duration_seconds`/`WorkflowSuspended` promoted to core, `RunConfig` frozen
  dataclass introduced, `adk_compat.py` seam added, nightly ADK canary job
  (`C24.18`–`C24.20`).
- Public API trimmed: `__all__` now lists only the core load/run/translate/
  history surface; SSO/RBAC/audit/interop names are provisional (`C24.21`,
  `C24.22`).
- Heavy dependencies moved behind `bedrock`, `containers`, `grpc`, `redis`,
  `database`, and `all` extras (`C24.23`).
- Root `conftest.py` centralizes the Docker skip helper and registers the
  `integration` marker; CI splits fast-unit and Docker-backed integration jobs
  and tracks skip counts (`C24.24`).
- `tools/` split into `interop/` and `devtools/` with backward-compatible
  facades (`C24.25`); translator BFS uses a deque (`C24.26`);
  `(namespace_id, created_at DESC)` index added (`C24.27`); `catch`/`while`
  declared as explicit `Task` fields (`C24.28`).

## Unreleased

- **Security (C25): GitHub Actions supply-chain hardening.** All third-party
  actions are pinned to full commit SHAs with version comments (checkout,
  setup-python, codecov, sbom-action, codeql-action, dependency-review-action,
  gh-action-pypi-publish; setup-uv was already pinned), every checkout sets
  `persist-credentials: false`, benchmark dispatch inputs are passed through
  `env` instead of shell interpolation, `release.yml` permissions are
  job-scoped (`id-token: write` on publish, `contents: write` on release-notes,
  `contents: read` elsewhere), the version-check job no longer restores a
  shared setup-uv cache, and the GitHub Release is created with the runner's
  `gh release` CLI instead of a third-party action. `actionlint` is clean and
  Zizmor reports zero findings.

- **Added spec-parity features** (gap analysis against the OpenWorkflow v1.0.3
  schema and other implementors — SonataFlow, Synapse, Lemline, EventMesh):
  - **taskBase semantics for every task kind**: `if` conditional execution,
    `input.from` input filtering, `output.as` output transformation,
    `export.as` context export, and `timeout` enforcement (inline or
    `use.timeouts` reference) via a uniform translator wrapper
    (`tasks/base_semantics.py`).
  - **Spec retry policies**: `catch.retry` supports inline policies and
    `use.retries` references with `delay`, `backoff` (constant/linear/
    exponential), `jitter`, `limit.attempt.count`, `limit.duration`, and
    `when`/`exceptWhen` runtime expressions.
  - **Catch error filters**: `catch.errors.with` matches on error
    `type`/`status`/`title`/`instance`; non-matching errors propagate.
  - **Reusable error definitions**: `raise.error` accepts `use.errors`
    references; `${...}` fields are evaluated against `$workflow.definition`
    and `$context`.
  - **Listen consume policies**: `until` conditions (including the spec's
    `( . | length ) > n` idiom), `foreach` per-event iteration with child
    tasks, and `correlate` filters with first-value and `expect` matching.
  - **`use.extensions` task injection**: `extend` (task kind or `all`),
    `when` gating, and `before`/`after` task injection around matching tasks.
  - **Document-level I/O filters**: workflow `input.from` (resume-aware) and
    `output.as`.
- **Fixed**: ADK `DynamicNodeFailError` wrappers are unwrapped before catch
  filters and retry policies inspect them (`adk_compat.unwrap_dynamic_error`).
- **Fixed**: expression rewrites — standalone `.` (current input) is
  translated mid-expression, and `( x | length )` becomes `$count(x)` so
  spec-canonical examples evaluate on the vendored JSONata engine.
- **Fixed**: state-schema derivation now declares keys written by
  `output.as`/`export.as` object literals, listen `foreach` iterators, and
  `use.extensions` tasks, so ADK state validation accepts them.
- **Docs**: rewrote `docs/reference/task-coverage.md` as an accurate
  feature matrix (the previous table understated shipped coverage).
- **Breaking**: ADK extensions now live in OpenWorkflow-compatible metadata
  containers. Task-level config goes in `task.metadata.adk` (`agent`,
  `self_heal`); project-level registries go in `document.metadata.adk`
  (`models`, `providers`, `memories`). The legacy direct forms (`agent:`,
  `self_heal:`, `use.models:`, `use.providers:`, `use.memories:`) are removed.
- **Breaking**: Catalog mode is removed. The project now supports only the
  extended flavor; `use.catalogs` is ignored by the translator.
- Added `owf-adk export --format openworkflow` and `owf-adk lint --strict`
  helpers for interoperable pure-OpenWorkflow output.

## 0.2.0 — 2026-08-10

- Added catalog mode for spec-pure workflows with reusable external function
  catalogs and `--mode auto|extended|catalog` selection.
- Split task translation into focused builder modules and mirrored the layout
  in tests and documentation.
- Added catalog examples, flavor documentation, coverage enforcement, and
  release/CI path updates.

## 0.1.0

- Added OpenWorkflow 1.0.3 validation and ADK translation.
- Added HTTP, OpenAPI, gRPC, AsyncAPI, A2A, MCP, shell, script, container, and subflow handlers.
- Added control flow, scheduling, durable sessions, run history, diagnostics, security guards,
  Docker packaging, fixtures, and the examples gallery.

Release notes follow a semantic-versioning policy. Changes are grouped under Added, Changed,
Fixed, and Security in future releases.
