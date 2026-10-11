# TODO — open-workflow-agent-adk

## Status

`main` is at **v0.2.1**: the v1 workflow runtime, production hardening, tests,
Docker support, CI, and release packaging are complete.

Merged since v0.2.1:

- **Spec parity (v1.0.3)**: taskBase `if`/`input`/`output`/`export`/`timeout`,
  catch retry policies and error filters, reusable
  `use.errors`/`use.retries`/`use.timeouts`, listen
  `until`/`foreach`/`correlate`, `use.extensions` injection, and
  document-level input/output filters. See
  [`docs/reference/task-coverage.md`](docs/reference/task-coverage.md).
- **C25**: GitHub Actions supply-chain hardening (SHA-pinned actions,
  `persist-credentials: false`, job-scoped permissions). `actionlint` clean,
  Zizmor zero findings.

Resolved in the current cleanup:

- `use.catalogs` resolves versioned `functions/<name>/<version>/function.yaml`
  resources, including GitHub/GitLab repository URLs and local catalog roots.
- `listen.to.until` supports both runtime expressions and nested event
  consumption strategies; terminator events are excluded from task output.
- `listen` supports `read: data`, `read: envelope`, and `read: raw`.

Reference material lives in [`docs/`](docs/):

- [Architecture](docs/reference/architecture.md)
- [Configuration](docs/reference/configuration.md)
- [Extension spec](docs/reference/extension-spec.md)
- [Task coverage](docs/reference/task-coverage.md)
- [Flavors](docs/flavors.md)
- ADRs in [docs/decisions/](docs/decisions/)

Spec baseline: **OpenWorkflow v1.0.3**. Run `spec-drift-check` before any schema work.

## Post-v0.2.1 cleanup (completed)

Findings from the review of `de05106..5c0964e` (v0.2.1 hardening + spec parity),
verified against the working tree on 2026-08-17. The non-Docker test suite is
green, Ruff/import-linter/actionlint/Zizmor are clean, and package metadata
checks pass. The detailed items below are completed implementation notes.

High:

- **Exec-expression validation misses `run.container.arguments` /
  `run.container.environment`** — `loader.py:200-212` rejects `${...}` bindings
  in `run.shell.command`, `run.script.*`, `run.container.image`, and
  `run.container.command`, but not `arguments` or `environment` values, which are
  bound from workflow state at runtime (`run.py:_wait_for_container`). Same
  prompt-injection → code-execution class the check exists to block. Add the
  fields to `exec_fields` plus a test.
- **`export_temporal` emits code importing `temporalio`, which is not a declared
  dependency or extra** — `interop/exports.py:14`. Generated code cannot run as
  written. Either add a `temporalio` optional extra or document the external
  requirement in the docstring.

Medium:

- **`WorkflowRegistry.resolve(version="latest")` sorts version strings
  lexicographically** — `registry.py:59`; `"1.0.10" < "1.0.2"` selects the wrong
  document for multi-digit versions. Use PEP 440 (`packaging.version.Version`)
  comparison and add a regression test.
- **Resume on `fork` round-trips every branch through pydantic** —
  `runtime.py:426-439` re-validates and re-dumps all branches
  (`model_validate` → `model_dump(exclude_none=True)`), which can drop or
  renormalize unknown/None-valued fields. Preserve the raw branch dicts instead
  of round-tripping.
- **`CatalogConfig` dropped from package surface but still public in
  `models.py`** — removed from `__init__.py` imports/`__all__`, yet still
  importable from `openworkflow_adk.models` and referenced by
  `UseDefinition.catalogs`. Decide: re-export shim with `DeprecationWarning` or
  remove from `models.py`.
- **`release.yml` publish job has no `contents: read`** — `release.yml:50-51`
  job-scoped `permissions: id-token: write` replaces the top-level
  `contents: read`, so `actions/checkout` (line 53) fails with 403. Add
  `contents: read` alongside `id-token: write`. (Still present after the C25
  hardening commit.)

Low:

- **Retry × self-heal composition is undocumented** — `control_flow.py:142-179`.
  Bounded (additive: `retry_attempt` and `heal_attempt` are both sticky), but add
  a test exercising heal-continue followed by retry-exhausted and a docstring
  note that retry + heal compose additively.
- **`catch.retry.limit.attempt.count` semantics ambiguous** —
  `control_flow.py:74`: `count` is total attempts (count:1 → no retries).
  Document.
- **Error filter matches only spec key `details`** — `control_flow.py:53` maps
  `("details", error.detail)`; a filter using `detail` silently never matches.
  Accept both spellings.
- **`.env.example` expression-timeout default out of sync** —
  `.env.example:38` documents `WORKFLOW_EXPRESSION_TIMEOUT_SECONDS=5`, code
  default is `0.25` (`expressions.py:70`). Align.
- **Diagnostics LSP server leaks `str(error)` to client** —
  `devtools/diagnostics_server.py:155-156`. Log the real error server-side,
  return generic `-32603` message (matches C24 error-hygiene posture).
- **Signal-handler timeout raise in `expressions.py:56-64`** — verify `jsonata`
  is pure Python (raise from a signal handler is unsafe inside C code) or switch
  to `PyThreadState_SetAsyncExc`; document the residual risk.
- **Global `WORKFLOW_EGRESS_SKIP_DNS=1` in `tests/conftest.py:19`** disables
  fail-closed DNS resolution for the whole suite; future SSRF tests can silently
  pass. Scope it to opting-in tests.
- **`tools/benchmark.py` shim omits `main()`** — devtools.benchmark exposes
  `main()`/`__main__`; the shim re-exports only `benchmark`.
- **`interop/exports.py:24` activity-name sanitize** can yield empty or Python
  keyword names; guard by prefixing `activity_`.
- **`devtools/diagnostics.py:78-83` duplicate-task diagnostic** emitted once per
  occurrence; dedupe to a single report.
- **Verify under Docker** — `tests/ops/test_postgres_history.py` remains the
  only pending validation because no Docker daemon is available here.

## Future work

These items are intentionally uncommitted in the current release line:

- **A2A adapter**: expose a workflow as an ADK-compatible agent.
- **MCP adapter**: expose workflow tasks as MCP tools.

## Archive

Completed backlog detail (C1–C25 and earlier phases) is preserved in git
history, the `v0.1.0` / `v0.2.0` / `v0.2.1` tags, and `CHANGELOG.md` (the
Unreleased section covers the post-v0.2.1 spec-parity work and C25).
