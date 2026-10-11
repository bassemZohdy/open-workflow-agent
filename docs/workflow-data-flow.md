# Workflow data flow

Core owns the data pipeline for all Python adapters. It follows the
[pinned OWS DSL](https://github.com/open-workflow-specification/specification/blob/2dd2c84170d5f3e05d58e913e9ca298dcf8d543a/dsl.md)
without changing the official schema. These stages execute in order:

| Stage | Value and behavior |
| --- | --- |
| Workflow input | Validate raw invocation data with `input.schema`, then evaluate `input.from`; initialize context from the transformed value. |
| Task condition | Evaluate `if` against raw task input. A skipped task passes that input through and does not validate, transform, export or invoke an operation. |
| Task input | Validate raw task input, then evaluate `input.from`. The task body and `$input` receive the transformed input. |
| Task output | Capture raw operation output in `$task.output`, evaluate `output.as`, then validate transformed output. |
| Task export | Evaluate `export.as` against transformed output with `$output` available; validate the proposed context and replace the previous context. Without `export.as`, validate and preserve existing context. |
| Workflow output | Evaluate `output.as` against the final task output, then validate the result. |

Context can be any admitted JSON type. Export replaces it entirely; it does
not merge object keys. Nested sequential tasks and loop iterations carry
successful exports forward. Task-local input, output and descriptors do not
leak into siblings or overwrite an enclosing task's descriptor. Each fork
branch receives copied input, variables and context. The branch aggregation,
reducer and competing-winner contracts remain tracked by S2-01 and S2-02.

Expression arguments have phase-specific scope:

| Scope | Available arguments |
| --- | --- |
| Workflow input transformation | `$workflow`, `$runtime` |
| Task condition/input transformation | `$workflow`, `$runtime`, `$context`, `$task`, enclosing loop/catch bindings |
| Task body/output transformation | Above, plus `$input` |
| Task export transformation | Above, plus `$output` |
| Workflow output transformation | `$workflow`, `$runtime`, `$context` |

The bounded event/resume profile also supplies `$event` and `$resume_input`
to task scopes when those values exist. Loop and catch names must be valid
jq identifiers and cannot shadow runtime arguments. Strict admission rejects
out-of-scope variables before effects. Existing loose-mode fallback rules
remain described in [expression semantics](workflow-expressions.md).

`$runtime` exposes the runtime name/version and adapter name in metadata.
`$workflow` exposes the invocation id, original parsed definition, raw input
and start time. `$task` exposes its name, original definition, raw input,
start time and original task reference; raw output becomes available after
the operation. Start times contain UTC ISO 8601, integer epoch seconds and
total epoch milliseconds. They are captured once for that scope. Descriptor
wrappers do not reduce the documented maximum user-data depth.

Invocation identity, lifecycle controls and service handles are private
execution metadata. Context exports cannot change cancellation, user identity,
event correlation or operation ids. Authorization and secrets arguments
remain unavailable until their deployment/security contracts are implemented;
no credentials or placeholder authorization values are fabricated.

Data-schema errors use the common validation error fields, preserve the
faulting task pointer and lifecycle, and omit the rejected value. Workflow
output validation failures fault the workflow after its tasks complete.
The unchanged schema, finite JSON/depth limits and unsupported external schema
boundary are covered in [admission](workflow-admission.md).

Evidence is in [shared data-pipeline fixtures](../tests/contract/test_data_pipeline.py),
[core jq tests](../tests/core/test_expressions.py), the contract lifecycle
suite and the adapted CTK transform fixture. This does not claim later-stage
transition/recovery/reducer semantics, JS delivery, all proposed inventory
tests or full OWS conformance.
