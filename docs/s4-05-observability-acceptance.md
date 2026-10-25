# S4-05 observability and recovery: authored acceptance slice

Status: S4-05 remains open. The corrected consolidated local batch and focused
metrics/correlation selections pass; exact-source hosted qualification remains
pending. The runtime exposes bounded in-process lifecycle events, low-cardinality
workflow and task event metrics, workflow duration, and an authorized
common-evidence recovery diagnostic. Its lifecycle replay is process-local and
does not claim native checkpoint readiness.

This batch adds `owa_task_duration_seconds` for completed, faulted, and
cancelled task events. The histogram clamps negative duration to zero and uses
only the engine and terminal outcome as labels. It does not label invocation,
session, task, operation, workflow input, or error details. Authored tests cover
all three terminal outcomes, a nonterminal event, negative duration, rendered
histogram samples, and identifier exclusion. API and configuration docs
describe the metric as in-process and runtime-local.

An additional bounded metrics correction derives workflow terminal status from
the fixed lifecycle event type instead of trusting the event's status field.
This keeps `owa_workflow_finished_total` at the three canonical outcomes even
if malformed event data supplies arbitrary status strings; the regression is
covered by the corrected consolidated local batch.

This source batch also propagates a child's persisted parent invocation and
canonical parent task reference on an operator-issued `WorkflowCancelled`
event when the child has no active worker. A focused regression asserts the
history link. Its focused local selection passes; it adds no new identity source
and makes no durable-history claim.

The broader S4-05 scope remains open: durable execution history across process
restarts, explicit trace-context and causation propagation, richer authenticated
provenance, lifecycle correlation, and replay diagnostics tied to common
evidence. Native checkpoints and engine-private state remain private; current
diagnostics do not claim to prove resumability. No Open Workflow schema or
CloudEvents contract changes are included in this slice.

The consolidated local batch passed its applicable metrics/API tests. Before
any S4-05 checkbox or feature status changes, required root, engine, database,
documentation, security, and image gates must pass on one exact source commit.
