# Repository Guidelines

## Autonomous Work

Treat `Project Definition.md` as authoritative; read it before implementation. Use `PROJECT.md` for orientation and `TODO.md` for the scoped backlog. Follow milestone order except for the user-authorized blocked-task deferral below. Update checkboxes only after verification and do not redesign contracts. `/init` changes are context and scaffolding only.

Do not remain indefinitely on a stuck task. After repeated unsuccessful attempts, if there is no actionable next step, move its original unchecked entry to the end of `TODO.md` under `Deferred tasks`, preserving its ID, scope and verified history. Record the exact blocker, attempts and evidence, remaining implementation and validation, affected dependencies, and the condition and next action for resuming it. Reflect the deferral in `PROJECT.md` and the feature tracker without marking the task or its features complete. Continue the next ready backlog task, including a later milestone when necessary; preserve dependency and capability gates. A running validation, a difficult task or a reproducible gap with an actionable fix does not by itself establish that work is stuck.

## Structure

`core/` owns framework-neutral contracts and services. `engines/adk/`, `engines/langgraph/`, and `engines/agent-framework/` own their adapters, dependencies, locks, and images (Agent Framework remains an optional native package, not a production image/release target). `sandbox-controller/` and `kubernetes-sandbox-controller/` own the restricted external-sandbox controllers. Shared fixtures belong in `tests/contract/`; engine and container tests belong in their corresponding test directories. Runtime functions and Open Workflow resources belong under `runtime-catalog/` and `resources/`.

## Critical Constraints

- Open Workflow 1.0.3 is the public DSL. Do not modify its official schema or expose the internal execution plan. The accepted 2026-10-11 roadmap permits versioned custom metadata extensions for declared native capabilities; unsupported required features must fail admission. Extension implementation remains staged work.
- Every invocation runs a workflow; missing workflow means the generated one-task workflow calling `agent:1.0.0@default`.
- The plan is typed, immutable, derived, and internal. Core owns loading, validation, `jq` semantics, normalization, catalog resolution, common services, and runtime errors.
- Core must not import ADK or LangGraph. Engine state and checkpointing remain engine-native; public identity and workflow fingerprints remain common.
- Keep `agent` and `llm`, knowledge and memory, session and checkpointing, and agent tools and workflow calls separate.
- Portability requires identical shared fixtures and expected results on both engines; advertise differences through capabilities.
- Executable workflow operations must use the common sandbox execution contract. ADK and LangGraph must never create independent subprocess, Docker, or Kubernetes execution paths.
- The internal sandbox is a controlled execution boundary, not a hard isolation boundary. Do not describe subprocess restrictions as equivalent to container, pod, VM, or microVM isolation.
- Docker/Kubernetes remain optional deployment-selected backends, not core runtime prerequisites. The internal sandbox and the restricted controller boundary precede and underpin them.
- The accepted consolidation roadmap in `TODO.md` expands the earlier completed core scope. Reuse native graphs/tasks/checkpoints where semantics match; default to one container and embedded storage with optional external bootstrap bindings. Track all gaps without treating research rows as shipped capabilities.
- Frozen predecessor material in `migration/reference/` is not active runtime code or a conformance oracle. Preserve provenance. Do not publish private source or copy unlicensed predecessor code without resolved transfer/license disposition.

## Development and Verification

Use strict typed Python, four-space indentation, deterministic fakes, and no paid APIs in tests. Keep engine dependency environments independent and never install packages at container startup or as part of sandbox execution. Preserve Open Workflow task references, translate framework exceptions into the common error contract, and run the relevant contract suite after engine changes.

For sandbox work, follow `docs/sandbox-execution.md` and the `DEPLOY-1` acceptance record in `TODO.md`/`PROJECT.md` (`DEPLOY-1` owns Kubernetes/OpenShift real-cluster acceptance). The internal sandbox and Docker backend are merged with acceptance recorded in `PROJECT.md`; do not advertise `run.script`, `run.shell`, or `run.container`, or the Kubernetes/OpenShift backend, beyond the acceptance gates already proven green.

## Security

Treat user input as untrusted. Never log secrets or place credentials in ordinary workflow files. Shell, script, container execution, and external catalogs are disabled by default. Protocol clients require timeouts, TLS verification, response-size limits, redirect policy, and authentication abstraction.

For internal process execution, do not inherit the full runtime environment, runtime working directory, unrestricted file descriptors, or unlimited output/time/resources. Preserve a dedicated execution workspace and capability-advertise only controls actually enforced by the platform.

For external sandbox execution, do not mount an unrestricted Docker socket into the Open Workflow Agent runtime and do not give the runtime cluster-wide Kubernetes/OpenShift permissions. Use a separate/restricted execution control boundary with least-privilege access.
