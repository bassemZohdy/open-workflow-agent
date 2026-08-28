# Contributing

Thank you for considering a contribution to the Open Workflow Agent.

## Read First

1. [`Project Definition.md`](Project%20Definition.md) — authoritative architecture and product contract.
2. [`AGENTS.md`](AGENTS.md) — mandatory repository rules (structure, constraints, verification, security).
3. [`TODO.md`](TODO.md) — active ordered backlog; pick from the ordered sections and work in milestone order.
4. [`PROJECT.md`](PROJECT.md) — verified implementation status and current acceptance record.
5. [`docs/development.md`](docs/development.md) — developer guide with the architecture boundary and commands.

## Ground Rules

- Open Workflow 1.0.3 is the only public DSL. Do not modify its schema or expose the internal execution plan.
- Keep `core/` framework-neutral; engine-specific code belongs in `engines/adk/`, `engines/langgraph/`, or `engines/agent-framework/`.
- Executable workflow operations must go through the common sandbox execution contract; engines never create independent subprocess/Docker/Kubernetes paths.
- Keep capabilities fail-closed until deterministic contract tests and the relevant acceptance gates are green.
- Never require paid model/API access in tests; use the deterministic `FakeModel`/hash-embedding fixtures.

## Development Workflow

```bash
uv sync --locked
uv run ruff format core engines tests
uv run ruff check .
uv run mypy core/src
uv run pytest -q
```

Engine contract suites (run after engine changes):

```bash
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
```

See [`docs/development.md`](docs/development.md) for the full command reference, including Docker builds and lock checks.

## Submitting Changes

1. Add or update tests before marking backlog items complete; keep engine dependency environments independent.
2. Run the relevant contract suite after engine changes and record verification honestly in `TODO.md`/`PROJECT.md`.
3. Open a pull request with a clear description of scope and verification evidence. Keep changes scoped to the backlog item you are addressing.
4. For security-sensitive changes, follow [`SECURITY.md`](SECURITY.md) and never include secrets, credentials, or real endpoints in tests, examples, or logs.
