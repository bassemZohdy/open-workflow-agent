# Contributing

## Workflow

1. Open an issue or RFC for behavior that changes the DSL, runtime contracts,
   security model, or persistence format.
2. Keep changes focused and add an acceptance test for each TODO item.
   The two integration tests that use Testcontainers are skipped when Docker
   is unavailable; run them in an environment with Docker and the daemon
   enabled when changing those integrations.
3. Run `uv run ruff check .`, `uv run ruff format --check .`, and
   `uv run pytest -q` before submitting a pull request.
4. Maintainers triage issues weekly; security reports should use private
   disclosure and are acknowledged within five business days.

Type checking is not currently a required CI gate. The package ships
`py.typed`, while Ruff and pytest are the enforced quality checks. A type
checker may be added later if the project adopts a repository-wide
configuration.

The current test coverage baseline is 83% (measured with
`uv run pytest --cov=openworkflow_adk`). CI protects the baseline with an 80%
minimum to allow for small platform and dependency-version differences.

## Decision records

Architecture changes belong in `docs/decisions/` with status, alternatives,
trade-offs, migration impact, and a rollback plan. Breaking changes require a
release-note entry and an explicit compatibility decision.
