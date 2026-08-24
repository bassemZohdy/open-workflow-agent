# Repository Guidelines

## Autonomous Work

Treat `Project Definition.md` as authoritative; read it before implementation. Use `PROJECT.md` for orientation and `TODO.md` for the scoped backlog. Work in the prescribed milestone order, update checkboxes only after verification, and do not implement later milestones or redesign contracts. `/init` changes are context and scaffolding only.

## Structure

`core/` owns framework-neutral contracts and services. `engines/adk/` and `engines/langgraph/` own their adapters, dependencies, locks, and images. Shared fixtures belong in `tests/contract/`; engine and container tests belong in their corresponding test directories. Runtime functions and Open Workflow resources belong under `runtime-catalog/` and `resources/`.

## Critical Constraints

- Open Workflow 1.0.3 is the only public DSL. Do not modify its schema or expose the internal execution plan.
- Every invocation runs a workflow; missing workflow means the generated one-task workflow calling `agent:1.0.0@default`.
- The plan is typed, immutable, derived, and internal. Core owns loading, validation, `jq` semantics, normalization, catalog resolution, common services, and runtime errors.
- Core must not import ADK or LangGraph. Engine state and checkpointing remain engine-native; public identity and workflow fingerprints remain common.
- Keep `agent` and `llm`, knowledge and memory, session and checkpointing, and agent tools and workflow calls separate.
- Portability requires identical shared fixtures and expected results on both engines; advertise differences through capabilities.

## Development and Verification

Use strict typed Python, four-space indentation, deterministic fakes, and no paid APIs in tests. Keep engine dependency environments independent and never install packages at container startup. Preserve Open Workflow task references, translate framework exceptions into the common error contract, and run the relevant contract suite after engine changes.

## Security

Treat user input as untrusted. Never log secrets or place credentials in ordinary workflow files. Shell, script, container execution, and external catalogs are disabled by default. Protocol clients require timeouts, TLS verification, response-size limits, redirect policy, and authentication abstraction.
