# Sandbox Execution Architecture

This document defines the approved execution-isolation architecture for Open Workflow Agent. The internal sandbox, the Docker external backend with its restricted Unix-socket controller, and the Kubernetes/OpenShift controller boundary are implemented behind deployment-controlled configuration (`sandbox:` in the runtime configuration; see [configuration.md](configuration.md)).

Default behavior remains fail-closed: `run.shell`, `run.script`, and `run.container` are rejected unless the deployment explicitly enables a sandbox backend, and `/v1/capabilities` reports only the controls actually enforced by the selected backend. Kubernetes/OpenShift container execution is advertised only after its real-cluster acceptance gates are green (see `TODO.md` B-006.3).

## Goals

Open Workflow Agent needs a common execution boundary for workflow operations that may execute code or operating-system processes. The first implementation must work inside the normal runtime image without requiring a Docker Engine, Kubernetes, or OpenShift cluster.

The design must:

- remain framework-neutral and identical for ADK and LangGraph;
- keep Open Workflow as the public DSL;
- prevent either engine adapter from invoking arbitrary OS processes directly;
- provide bounded execution, cancellation, observability, and policy enforcement;
- distinguish controlled internal execution from strong external isolation;
- allow stronger Docker/Kubernetes/OpenShift backends later without changing workflow definitions;
- fail closed when an execution type or requested capability is not supported.

## Execution levels

The runtime distinguishes three levels of execution.

```text
Open Workflow task/function
          |
          v
    Execution Services
          |
          +-- Level 1: Managed function boundary
          |      trusted runtime/catalog functions
          |      capability-scoped services
          |      normally no child OS process
          |
          +-- Level 2: Internal process sandbox
          |      local command/script execution
          |      controlled child process
          |      no Docker/Kubernetes dependency
          |
          +-- Level 3: External sandbox
                 Docker / Kubernetes / OpenShift
                 stronger isolation boundary
```

Level 1 and Level 2 form the internal sandbox foundation. Level 3 is the optional external backend family selected through deployment configuration.

## Security terminology

The **internal sandbox is a controlled execution boundary, not a hard security boundary**.

A child process started inside an Open Workflow Agent container still shares the container's kernel, namespaces, network namespace, and some filesystem visibility. Timeouts, environment filtering, working-directory restrictions, process limits, and API-level policy reduce risk, but they do not provide the same isolation as a dedicated container, pod, microVM, or other external sandbox.

Therefore documentation and capabilities must never describe the internal backend as equivalent to container or VM isolation.

## Common architecture

Sandbox semantics belong in the common core. ADK and LangGraph only execute the canonical task plan and delegate executable operations to common execution services.

```text
Open Workflow
      |
      v
Canonical Execution Plan
      |
      v
Run / Function Executor
      |
      v
SandboxManager
      |
      +-- InternalSandboxBackend
      |
      +-- DockerSandboxBackend        (restricted controller boundary)
      |
      +-- KubernetesSandboxBackend    (restricted controller boundary)
```

Engine adapters must never contain independent `subprocess`, Docker, or Kubernetes execution paths for portable workflow semantics.

## Sandbox manager contract

The exact Python API may evolve during implementation, but the common contract should model concepts equivalent to:

```python
class SandboxBackend(Protocol):
    async def execute(
        self,
        request: SandboxExecutionRequest,
    ) -> SandboxExecutionResult:
        ...

    async def cancel(self, execution_id: str) -> None:
        ...

    def capabilities(self) -> SandboxCapabilities:
        ...
```

A request should carry only engine-neutral execution information, for example:

```text
SandboxExecutionRequest
├── execution_id
├── invocation_id
├── task_reference
├── execution_type
├── executable/runtime
├── arguments
├── stdin
├── environment references
├── working files
├── timeout
├── output limits
├── resource limits
└── policy requirements
```

The public workflow must not contain backend-specific Docker IDs, Kubernetes pod names, ADK run IDs, or LangGraph checkpoint identifiers.

## Managed function boundary

Trusted built-in runtime functions such as `agent:1.0.0@default` and `llm:1.0.0@default` remain managed runtime functions; they should not be converted into child processes merely to claim sandboxing.

Managed functions should receive only the common services they require. New executable/plugin-style functions must not receive unrestricted access to runtime internals by default.

Where practical, capability-scoped services should be used instead of direct global access:

```text
function context
├── input
├── cancellation
├── approved filesystem/workspace access
├── approved network/protocol service
├── approved secret references
└── observability context
```

This is a programming and policy boundary. It does not make arbitrary in-process Python code safe against a hostile plugin. Arbitrary Python plugins remain outside the current product contract.

## Internal process sandbox

The first executable backend is `InternalSandboxBackend`. It is implemented and shared by both engines through `SandboxManager`.

Minimum behavior:

- use an argument-vector API rather than implicit shell interpolation whenever the Open Workflow operation permits it;
- do not inherit the complete runtime environment by default;
- provide an explicit environment allowlist/injection model;
- create a dedicated temporary working directory for each execution;
- do not inherit the runtime's current working directory as an execution workspace;
- bound stdin size, stdout size, stderr size, and total captured output;
- enforce an execution timeout;
- support cancellation and terminate the complete child process tree where the platform permits it;
- close inherited file descriptors except those explicitly required;
- apply supported OS resource limits such as CPU/process/file-size/address-space limits where practical;
- clean the temporary workspace after completion according to policy;
- return structured exit status, bounded output, timing, and sanitized failure information;
- emit common lifecycle/observability events using the Open Workflow task reference.

The internal backend must not claim capabilities that the host operating system cannot enforce consistently. Platform-specific limits must be represented through capabilities and tested on supported platforms.

## Filesystem policy

The internal process backend should operate in a dedicated temporary workspace.

The intended default is:

```text
runtime configuration   not writable by child
knowledge               not writable by child
runtime persistent data not exposed unless explicitly required
workspace               writable and execution-scoped
```

A temporary directory is not a kernel-enforced filesystem sandbox by itself. The implementation must document exactly which paths remain visible to the child process on each supported platform.

Host-path mounts requested by a workflow must not be introduced as part of the internal sandbox profile.

## Environment and secrets

The child process must not inherit all runtime environment variables. This is particularly important because model, database, catalog, MCP, A2A, and other credentials may exist in the runtime environment.

Secrets must be supplied only through an explicit deployment-controlled reference/policy. Workflow input must not be allowed to name arbitrary runtime environment variables and retrieve their values.

Logs and execution results must never contain secret values intentionally injected by the runtime.

## Network policy

The internal backend may be unable to provide kernel-level network isolation without additional operating-system facilities. The initial implementation must therefore distinguish:

```text
network API policy      enforceable by common runtime services
process network denial  only advertised when actually enforced
```

A child process must not be described as network-isolated merely because the workflow was not given a network helper.

Strong egress isolation is one reason to select a future external sandbox backend.

## Open Workflow routing

Different Open Workflow operations use different common services.

```text
agent / llm catalog calls
    -> managed runtime functions

http / MCP / A2A / OpenAPI calls
    -> common bounded protocol services

run.workflow
    -> Invocation Service / child workflow execution

run.script
    -> SandboxManager when the internal script profile is enabled

run.shell
    -> SandboxManager when the internal shell profile is enabled

run.container
    -> SandboxManager only when a container-capable external backend is enabled
```

`run.workflow` must not launch a sandbox process merely because it is a `run` task.

## Shell execution

Shell execution is higher risk than direct executable invocation.

`run.shell` is available only when the deployment sets `sandbox.allow_shell: true` with the sandbox backend enabled:

- workflow data must not be concatenated into an implementation-created shell command string;
- the runtime preserves the Open Workflow-defined command semantics rather than inventing another templating language;
- timeouts, environment policy, output limits, workspace policy, cancellation, and resource controls remain mandatory;
- unsupported shell features fail closed.

The implementation prefers direct executable/argument execution for operations that do not semantically require a shell.

## Script execution

`run.script` supports explicit, capability-advertised runtimes (`sandbox.script_runtimes`, `python` by default).

The runtime never dynamically installs interpreters or packages during startup or execution. Script runtimes must already exist in the selected release image or be provided by an external sandbox image.

Arbitrary dependency installation initiated by workflow input is outside the internal sandbox profile.

## External sandbox backends

External backends depend on the internal `SandboxManager` contract and are disabled by default.

### Docker backend

The Open Workflow Agent runtime container must not receive unrestricted `/var/run/docker.sock` access.

The implemented Docker backend talks to a separately controlled execution component — the restricted Docker sandbox controller (`sandbox-controller/`) — over a Unix socket, exposing only the operations required to create, start, wait for, inspect logs from, stop, and remove sandbox containers. The deployment pins approved images (digest-required by default), forces non-root execution, and denies container networking.

### Kubernetes/OpenShift backend

A Kubernetes/OpenShift backend creates isolated ephemeral Pods in a dedicated sandbox namespace/project using a narrowly scoped ServiceAccount held by the restricted controller (`kubernetes-sandbox-controller/`), reachable only on a loopback endpoint from the runtime.

Expected controls include non-root execution, no privileged mode, no host namespaces, no host-path mounts, resource requests/limits, bounded ephemeral storage, network policy, approved images/registries, and cleanup/TTL behavior. Real-cluster acceptance (including OpenShift SCC/security-context behavior) is still pending; see `TODO.md` B-006.3.

## Backend selection

Backend selection is deployment policy, not workflow authoring syntax.

The implemented configuration conceptually resembles:

```yaml
sandbox:
  enabled: true
  backend: internal   # internal | docker | kubernetes
  allow_shell: false
  script_runtimes: [python]
  timeout_seconds: 30
  max_output_bytes: 10485760
```

See [configuration.md](configuration.md) for the exact strict configuration model and [api.md](api.md) for the `features.sandbox` capability block. A deployment can select `docker` or `kubernetes` without changing the Open Workflow definition, provided the selected backend advertises the required capability.

## Capability model

`/v1/capabilities` must report sandbox execution explicitly before any executable workflow feature is advertised.

The capability model should distinguish at least:

```text
internal process execution
shell execution
script execution + supported runtimes
container execution
resource-limit enforcement
filesystem isolation level
network isolation level
cancellation
external backend type
```

A feature is portable only after the same Open Workflow fixture passes on ADK and LangGraph through the common sandbox service.

## Error model

Sandbox failures must translate into engine-neutral runtime errors. The implementation should distinguish errors such as:

```text
unsupported execution type
policy rejection
invalid executable/runtime
startup failure
timeout
cancellation
resource limit exceeded
output limit exceeded
non-zero exit
backend unavailable
cleanup failure
```

Error details must be bounded and sanitized. Engine/backend-native objects or credentials must not become part of the public API contract.

## Retry, resume, and side effects

Sandbox execution is not exactly-once merely because the surrounding workflow is durable.

The runtime must assume an executable task can be retried after an ambiguous failure or resume boundary. Side-effecting commands therefore require the same idempotency/deduplication discipline as HTTP, MCP, A2A, payment, email, or database-write operations.

Persist only the common execution metadata necessary for lifecycle and recovery. Do not make Docker/Kubernetes/process-native state part of the public invocation contract.

## Test requirements

Before `run.script` or `run.shell` can be advertised, deterministic tests must cover:

- timeout and cancellation;
- stdout/stderr and total output limits;
- environment filtering and secret non-leakage;
- temporary workspace creation and cleanup;
- invalid executable/runtime handling;
- non-zero exit behavior;
- process-tree termination;
- resource-limit behavior where supported;
- attempts to access disallowed runtime files/secrets;
- concurrency and cleanup under failures;
- identical ADK/LangGraph observable results;
- arbitrary UID and read-only-root container acceptance;
- capability reporting and fail-closed behavior.

Tests must not require a paid API, public network, Docker daemon, or Kubernetes cluster for the internal sandbox milestone.

External backend tests are separate and must prove their stronger isolation and infrastructure-specific controls before those backends are advertised.

## Delivery order

The required order was and remains:

```text
1. Sandbox contract and threat model
2. SandboxManager + capability/error models
3. Managed function capability boundary
4. InternalSandboxBackend
5. Process limits / cancellation / output handling
6. Internal script execution profile
7. Internal shell execution profile
8. Cross-engine and container acceptance
9. External Docker backend
10. External Kubernetes/OpenShift backend
11. Container execution profile
```

Steps 1-9 are implemented and accepted (Docker acceptance recorded green in `PROJECT.md`). Step 10/11 code is merged; Kubernetes/OpenShift remains gated on real-cluster acceptance before advertisement. The internal manager, policy model, and backend-neutral contracts remain the foundation for every execution mode.
