# External Sandbox Backend Contract

Status: B-006.1 implementation baseline.

## Goal

External sandbox backends add stronger isolation without changing Open Workflow engine adapters. ADK, LangGraph, and future engines continue to route executable operations through the common sandbox boundary.

```text
Workflow / engine adapter
        ↓
SandboxManager
        ↓
backend-neutral request + requirements
        ↓
┌────────────────┬──────────────────┬────────────────────┐
│ internal       │ Docker           │ Kubernetes/OpenShift│
└────────────────┴──────────────────┴────────────────────┘
```

Backend selection is deployment-controlled. Workflow documents never choose `internal`, `docker`, or `kubernetes` directly.

## Common requirements

`open_workflow_agent.sandbox_contract` defines portable requirements for:

- execution kind (`script`, `shell`, `container`);
- runtime selection;
- deployment-approved image selection;
- bounded staged input files;
- secret references;
- timeout/output/workspace/CPU/memory/file-size/process limits;
- filesystem isolation;
- network isolation;
- hard-isolation requirements.

A backend capability descriptor contains only common features and guarantees. It must not expose container IDs, Docker daemon objects, pod names, Kubernetes UIDs, node names, PIDs, process-group IDs, or framework-native state.

## Fail-closed compatibility

Deployment policy is checked against backend capabilities before execution. If the selected backend cannot enforce a requested guarantee, execution must fail with `SandboxPolicyError`; the runtime must not silently weaken policy.

Examples:

- `hard_isolation=true` on the internal backend → reject;
- `network=denied` on the internal backend → reject;
- `filesystem=isolated_root` on the internal backend → reject;
- image selection on a backend that only executes the host runtime → reject;
- container execution on the internal backend → reject.

The internal backend is intentionally translated as:

```text
filesystem = workspace_cwd_only
network    = unrestricted
hard       = false
```

This preserves the existing honest capability boundary.

## Docker backend boundary

The main runtime must **not** receive unrestricted `/var/run/docker.sock` access.

Preferred deployment:

```text
Open Workflow Agent
       ↓
restricted sandbox controller / Docker socket proxy
       ↓
Docker Engine
```

The controller/API should expose only the operations required by sandbox execution:

```text
create
start
wait
bounded logs
stop/kill
remove
```

Policy must enforce approved registries/images, digest pinning where configured, non-root user, read-only root filesystem where possible, no privileged mode, no host networking, no host PID/IPC namespaces, no host mounts, bounded resources, bounded logs, timeout, and unconditional cleanup.

## Kubernetes/OpenShift backend boundary

Preferred deployment:

```text
Open Workflow Agent
       ↓
namespace-scoped sandbox controller / ServiceAccount
       ↓
dedicated sandbox namespace/project
       ↓
ephemeral Pod/Job
```

The ServiceAccount must be unable to manage unrelated namespaces or workloads. Sandbox workloads must reject privileged mode, host namespaces, `hostPath`, unrestricted service-account token use, and unbounded resources. OpenShift acceptance must prove arbitrary-UID/SCC compatibility.

## Public lifecycle state

Public/common lifecycle events may include:

```text
execution_id
invocation_id
workflow/task identity
backend capability class (optional)
duration
status
sanitized error code
```

They must not include infrastructure-native execution handles. Native IDs remain private backend/controller state used only for cancellation and cleanup.

## `run.container`

`run.container` remains rejected until an external backend both:

1. advertises container execution in the common capability contract; and
2. passes deterministic acceptance for policy enforcement, cancellation, cleanup, output bounds, and hardened runtime isolation.

No internal-backend emulation of `run.container` is allowed.
