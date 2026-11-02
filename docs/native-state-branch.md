# Native state branch (opt-in source profile)

The LangGraph `langgraph.state.branch@1.0.0` profile creates a new invocation
from a completed root-task checkpoint, replacing its task data with a declared,
schema-validated object. The source invocation remains unchanged. This profile
uses common ownership and effect authorization with native checkpoints; it is
not full time travel, S4-02 completion, or a published-image qualification.

## Configure

The [bootstrap](../examples/native-state-branch.yaml) and
[workflow](../examples/native-state-branch-workflow.yaml) show a fake-model
example. Mount both under `/config`, provide `OWA_NATIVE_STATE_TOKEN` through
deployment secrets, and retain the runtime's data directory. The profile requires
`native_state`, `native_state_updates`, `native_state_branch`, authenticated API
ingress and execution authorization. The workflow must declare the read, update
and branch extensions; the branch and update policies must name the same
authorization policy. Grant `state.inspect` and `state.branch` to the original
owner, plus the actions needed by the workflow.

`native_state_branch.bounds` limits native inspection and staged packets.
`max_active_branches` defaults to two and cannot exceed the native concurrency
bound; `max_branches_per_source` defaults to eight. State-view, update, replay
and branch packet budgets share a 256 MiB ceiling when branch is enabled. Default
installations and engines without the configured native profile do not advertise
or admit required branch workflows.

## Create a branch

Invoke the example, then select a root history item from
`GET /v1/invocations/{id}/states`. Its `root_task_visits` must be a contiguous
completed prefix. Attribution must name the **last** completed root `set` task
and be allowed by the workflow's update policy. For the example, choose the
checkpoint after `second` and submit:

```http
POST /v1/invocations/{invocation_id}/state-branches
Authorization: Bearer <deployment token>
Idempotency-Key: branch-1
Content-Type: application/json

{"reference":"<opaque state reference>","task_reference":"/do/1/second","values":{"value":7},"acknowledge_reexecution":true}
```

The declared `branchEffectPolicy` is `read_only` or `reexecute`. The acknowledgement
must be true exactly for `reexecute`. A read-only target faults before executing
an external effect; re-execution records new target effects. A successful admission
returns `202` with the new `invocation_id`, `source_invocation_id`, `effect_policy`
and `state`. The target continues asynchronously and can be observed or cancelled
through the ordinary invocation APIs. Repeating the same key and request returns
the same target; changing the request under that key is rejected.

The original owner can also inspect the target's retained state and history through
the ordinary state views. Reads verify the retained lineage and fail closed if its
native proof or authority is unavailable or inconsistent.

Both the current caller and retained original authority must permit `state.branch`;
only the original owner can select the source. Unsupported checkpoints, invalid
data and acknowledgement mismatches fail before creating a target. Active-work
and per-source limits return `429`; a lineage target cannot be branched again.
After a process interruption, repeat the identical request and idempotency key.
An effect whose outcome is uncertain fails closed rather than being repeated.

## Current boundaries

The supported source is one root checkpoint with a contiguous completed-task
prefix, no completed nested scopes, timing, native channels, Command/Send routing
or prior state-control bindings. The branch changes only root task data. Arbitrary
attribution, nested scope remapping, branch-of-branch, time travel, independent
JavaScript parity and hosted/release qualification remain open. Local validation
and its retained failures are recorded in the
[lineage acceptance record](s4-02-native-lineage-acceptance.md).
