# Developer Guide

This guide is for contributors extending Open Workflow Agent itself.

Before changing architecture or public contracts, read:

1. `Project Definition.md` — authoritative architecture/product contract.
2. `PROJECT.md` — verified implementation status.
3. `TODO.md` — active ordered backlog.
4. `AGENTS.md` — mandatory repository rules.
5. `docs/sandbox-execution.md` — approved sandbox execution architecture and security boundary.

## Architecture boundary

The dependency direction is:

```text
API
 |
 v
Core
 |
 v
Engine SPI
 ^
 |
ADK / LangGraph
```

Core must not import ADK or LangGraph.

Core owns:

- configuration;
- Open Workflow loading and validation;
- portable capability checks;
- normalization/internal execution plan;
- jq/data semantics;
- catalog resolution;
- knowledge and memory abstractions;
- protocol services;
- invocation metadata;
- common errors and lifecycle events;
- sandbox policy, manager/backend contracts, and portable executable-task semantics.

Each engine owns:

- framework-native workflow execution;
- framework-native agent/tool integration;
- checkpoint/resume integration;
- engine-specific persistence;
- framework exception translation at the boundary.

Engines do **not** own subprocess, Docker, or Kubernetes execution for portable Open Workflow tasks. Executable tasks must delegate to the common sandbox execution service.

## Repository layout

```text
core/                     framework-neutral runtime
engines/adk/              ADK adapter and dependency lock
engines/langgraph/        LangGraph adapter and dependency lock
engines/agent-framework/  optional Microsoft Agent Framework adapter
sandbox-controller/       restricted Docker sandbox controller
kubernetes-sandbox-controller/  restricted Kubernetes/OpenShift sandbox controller
runtime-catalog/          built-in runtime functions
resources/                Open Workflow resources/schema assets
tests/core/               common unit/integration tests
tests/contract/           cross-engine portable fixtures
tests/adk/                ADK-specific tests
tests/langgraph/          LangGraph-specific tests
tests/ctk/                selected Open Workflow CTK coverage
tests/e2e/                container/end-to-end coverage
docker/                   engine Dockerfiles and entrypoint
deploy/                   Kubernetes/OpenShift deployment manifests
docs/                     user/operator/developer documentation
```

The sandbox implementation is a framework-neutral core package (`core/src/open_workflow_agent/sandbox/`) layered as: `contract.py` (the abstract interface), `manager.py` (backend selection), `validation.py` (the workflow-level capability gate), `executor.py` (workflow-facing routing), and `backends/` (the shared reusable utilities — internal process machinery plus the restricted-controller clients in `docker.py`/`kubernetes.py`; `sandbox_capabilities.py` holds the portable requirements contract). It never lives inside an engine package.

## Local setup

```bash
uv sync --locked --extra knowledge
```

The `knowledge` extra is included in the development and root CI environments
because the common suite exercises the local embedding provider; it remains
optional for runtime installations that do not mount or query knowledge.

Run the common test suite:

```bash
uv run pytest -q
```

Mutation testing runs on Linux because mutmut requires process-fork support.
The repository configuration limits the CI mutation scope to the traffic-policy
middleware and selects dedicated focused tests so the result remains a repeatable
quality signal rather than a full-suite performance job:

```bash
cp -R core/src src
uv sync --locked --group mutation
uv run --locked --group mutation mutmut run
uv run --locked --group mutation mutmut results
rm -rf src
```

The temporary `src` copy is needed because mutmut's module-key matching expects
a conventional top-level source root, while production sources remain under
`core/src`.

On Windows, run these commands inside WSL or rely on the Ubuntu CI job.

Run the dependency-free runtime benchmarks:

```bash
uv run python -m benchmarks.runtime --iterations 50 --concurrency 10
```

Formatting/lint/type checks:

```bash
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy --package open_workflow_agent \
  --package open_workflow_agent_adk \
  --package open_workflow_agent_langgraph \
  --package open_workflow_agent_agent_framework
```

Mypy runs strict over core and the three engine adapter packages. Native SDK imports
remain an explicit integration boundary: missing or incomplete SDK stubs are isolated
through the package-specific override, while adapter-owned signatures and logic remain
type-checked. The Agent Framework adapter is optional and is checked even when its
native dependency is not installed.

Build packages:

```bash
uv build
uv build --directory core
```

## Engine-specific tests

ADK:

```bash
uv run --directory engines/adk --locked --extra native --with pytest --with pytest-asyncio \
  pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
```

LangGraph:

```bash
uv run --directory engines/langgraph --locked --extra sqlite --with pytest --with pytest-asyncio \
  pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
```

Do not combine engine dependency locks into one environment just for convenience.

## Contract tests define portability

A capability is portable only when the same fixture and input produce the expected equivalent behavior on both engines.

Shared fixtures live under:

```text
tests/contract/fixtures
```

When adding portable behavior:

1. add/update a shared fixture;
2. add common semantic coverage;
3. make ADK pass it;
4. make LangGraph pass it;
5. update `/v1/capabilities` only after the contract is actually supported.

Do not silently ignore unsupported Open Workflow features.

For future executable tasks, the shared fixture must exercise the common `SandboxManager`; it must not validate two separate engine-owned subprocess implementations.

## Models in tests

Automated tests must not require paid APIs.

Use the common deterministic `FakeModel` for:

- simple responses;
- structured payloads;
- tool-call behavior;
- controlled failures/retries.

LiteLLM is optional runtime functionality, not a CI requirement.

## Adding a common feature

Examples: workflow semantics, protocol behavior, knowledge, memory, errors.

Keep implementation under `core/` when the behavior is engine-neutral. Expose only the minimum SPI required for engines to consume it.

Do not leak framework-native objects into public/runtime core models.

## Adding engine-specific behavior

Put framework integration under the corresponding engine package.

If an optional capability exists in only one engine, advertise it explicitly through capabilities rather than weakening the common contract or faking parity.

## Persistence rules

The common runtime owns public invocation identity, workflow fingerprint, status, and metadata.

ADK owns ADK durable/session state. LangGraph owns LangGraph checkpointer/store state.

Never make one engine read another engine's checkpoint representation.

Sandbox execution metadata follows the same portability rule: common execution identity/status may be persisted when required, but PIDs, Docker container IDs, Kubernetes Pod/Job names, and other backend-native identifiers must not become the public invocation contract.

## Workflow rules

Open Workflow 1.0.3 is the authoring DSL.

The internal canonical execution plan is:

- derived;
- typed;
- immutable;
- internal only.

Do not introduce a second external workflow language and do not fork the Open Workflow schema to add AI-specific calls. `agent:1.0.0@default` and `llm:1.0.0@default` are runtime catalog functions.

`run.workflow` remains child workflow execution through the invocation service. It must not be routed through a process/container sandbox merely because it is represented by an Open Workflow `run` task.

## Sandbox execution boundary

The approved roadmap is documented in [sandbox-execution.md](sandbox-execution.md).

The required order is:

```text
common SandboxManager/SPI
        |
        v
InternalSandboxBackend
        |
        +-- run.script
        +-- run.shell
        |
        v
external backends later
        +-- Docker
        +-- Kubernetes/OpenShift
        |
        v
run.container where supported
```

The internal backend must work without Docker or Kubernetes. It provides controlled process execution with bounded environment, workspace, input/output, timeout, cancellation, cleanup, and enforceable resource limits.

It is not a hard security boundary. A subprocess inside the runtime container still shares the container/host kernel and some namespaces/resources. Do not document or advertise stronger isolation than the implementation actually enforces.

Built-in `agent`/`llm` functions and bounded protocol calls remain managed runtime services; do not move them into subprocesses solely for conceptual uniformity.

## Security expectations

Treat invocation/user input as untrusted and workflow definitions as trusted deployment artifacts.

Do not:

- log secrets;
- put credentials in ordinary workflow examples;
- enable arbitrary shell/script/container execution before the corresponding sandbox milestone is accepted;
- execute shell/script directly from an ADK or LangGraph adapter;
- inherit the full runtime environment into child processes;
- use the runtime working directory as an unrestricted execution workspace;
- describe temporary-directory/process limits as equivalent to container/VM isolation;
- mount an unrestricted Docker socket into the runtime for external sandbox execution;
- grant the runtime cluster-wide Kubernetes/OpenShift permissions for sandbox workloads;
- bypass TLS/timeout/response-size protocol protections;
- dynamically install packages at runtime startup or as part of sandbox execution.

## Docker validation

Build both independently:

```bash
docker build -f docker/Dockerfile.adk .
docker build -f docker/Dockerfile.langgraph .
```

Compose validation:

```bash
cp .env.example .env
docker compose --profile adk up --build
# or
docker compose --profile langgraph up --build
```

Remote CI additionally validates image metadata/size, container acceptance, PostgreSQL persistence, selected CTK coverage, and stop/restart/resume behavior.

Container acceptance verifies internal sandbox behavior under arbitrary UID, read-only root filesystem, bounded `/tmp`, graceful SIGTERM, and secret-safe retained logs without requiring a Docker daemon or Kubernetes cluster inside the test container. Docker external-sandbox acceptance runs on the self-hosted Docker runner; Kubernetes/OpenShift real-cluster acceptance is tracked separately in `TODO.md` B-006.3.

### Base image updates

All runtime and controller Dockerfiles pin their base images by digest (`python:3.12-slim@sha256:...`, `docker:29.7.2-cli@sha256:...`). To update a base image:

1. Resolve the current multi-arch digest, for example:
   `docker buildx imagetools inspect python:3.12-slim --format '{{json .Manifest.Digest}}'`
2. Update the `ARG PYTHON_IMAGE` value in `docker/Dockerfile.adk` and `docker/Dockerfile.langgraph`, and the `FROM` lines in `docker/Dockerfile.sandbox-controller` and `docker/Dockerfile.kubernetes-sandbox-controller`.
3. Run the local image acceptance steps and the CI Docker jobs; the Security workflow and the Trivy release gate must also pass on the rebuilt image.

### Kubernetes sandbox acceptance with kind

To reproduce the B-006.3 Kubernetes acceptance locally:

1. Create a cluster with a NetworkPolicy-enforcing CNI (kindnet does not enforce policy):
   `kind create cluster --name owa-acceptance`, then install Calico (patch `CALICO_IPV4POOL_CIDR` to the kind pod CIDR `10.244.0.0/16`).
2. Apply the boundary: `kubectl apply -f deploy/kubernetes/sandbox-boundary.yaml`.
3. Deploy a runtime pod with the Kubernetes sandbox controller as a loopback sidecar: the controller container uses the `owa-sandbox-controller` service account with a projected service-account token at `/var/run/secrets/owa-controller` plus the `kube-root-ca.crt` ConfigMap; the runtime sets `sandbox.backend: kubernetes` with digest-pinned `allowed_images`, `network_policy_enforced: true`, and a `workflow.definition` exercising `run.container`.
4. `kubectl port-forward` the runtime port and drive `POST /v1/invoke`; verify execution, timeout (`sandbox_timeout`), cancellation, restart/ambiguous-failure cleanup (`activeDeadlineSeconds` + TTL), secret safety (`secretKeyRef`, no value in logs/responses), RBAC (controller token forbidden on secrets/pods/cross-namespace/cluster scope), and egress denial.
5. Teardown: `kind delete cluster --name owa-acceptance`.

### OpenShift sandbox acceptance

Run `tests/ci/openshift_sandbox_acceptance.sh` against a disposable OpenShift
project after publishing two digest-pinned images. Set
`OWA_K8S_CONTROLLER_IMAGE` to the Kubernetes/OpenShift controller image and
`OWA_SANDBOX_IMAGE` to the approved workload image. The script creates only a
project-scoped ServiceAccount, Role, RoleBinding, default-deny NetworkPolicy,
and controller Deployment; it removes the generated project on exit. When a
pre-created disposable project is required, set `OWA_OPENSHIFT_PROJECT` and
`OWA_OPENSHIFT_ALLOW_EXISTING=1`; the script then removes only its uniquely
named resources.

The acceptance checks the applied pod's `openshift.io/scc` annotation, confirms
that the restricted SCC injects a non-root UID while the controller template
does not hard-code one, verifies the controller process UID and security
context, checks the ServiceAccount's positive and negative RBAC permissions,
and invokes the controller. The sandbox execution must prove arbitrary
non-root UID execution, writable `/workspace`, read-only root filesystem, and
denied cluster egress. A successful local/static check or Kubernetes run does
not close `DEPLOY-1`; update that item only after this script passes against a
real OpenShift cluster.

Dependabot opens weekly update PRs for GitHub Actions versions, every `uv.lock`, and the base images in `docker/`; the Security workflow (pip-audit over every locked environment) and the release Trivy gate block publication on known fixable `CRITICAL`/`HIGH` image advisories. When a base bump does not clear an advisory because the upstream image has not been rebuilt yet, the finding is recorded with a dated rationale in `.trivyignore` and must be re-checked on every base refresh; findings in our own code or Python dependencies are never suppressed.

## Documentation rule

When changing public configuration, API behavior, supported workflow semantics, deployment requirements, or capabilities, update the corresponding file under `docs/` and keep README quick-start examples valid.

Implementation status belongs in `PROJECT.md`; active work belongs in `TODO.md`; architecture decisions belong in `Project Definition.md` and approved focused architecture documents such as `docs/sandbox-execution.md`. Avoid turning README back into a status log.
