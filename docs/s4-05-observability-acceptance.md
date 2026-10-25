# S4-05 observability and recovery: authored acceptance slice

Status: S4-05 remains open. The corrected consolidated local batch and focused
metrics/correlation selections pass; exact-source hosted qualification remains
pending. The runtime exposes bounded in-process lifecycle events, low-cardinality
workflow and task event metrics, workflow duration, and an authorized
common-evidence recovery diagnostic. Default lifecycle replay is process-local and
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

## Durable diagnostic history candidate — 2026-10-25

The opt-in `observability.history` candidate retains the existing sanitized
CloudEvents lifecycle projection in common persistent SQLite/PostgreSQL storage.
Its signal/schema remains `urn:open-workflow-agent:schema:lifecycle:1`, CloudEvents
1.0. No OpenTelemetry semantic convention or exporter is introduced. This slice
addresses S4-05 restart visibility and bounded replay, rather than an unmeasured
SLO. The runtime operator owns storage capacity and retention configuration;
there is no new alert or on-call policy.

Defaults are 1,000 events, 65,536 bytes per event, 16 MiB retained payload and
50 ms polling. Finite configuration caps remain enforced. The history uses a
monotonic common commit sequence, count/byte eviction and bounded subscribers.
Repeated identical retained event IDs are read-only; conflicting IDs reject.
Snapshot and stream reads retain the existing explicit `events.read` gate.
An expired cursor rejects; observer overflow or retention gaps end a stream
with bounded backpressure. The selected store is independent of native engine
checkpoints and has no broker dependency.

History contains only the current lifecycle allowlist: common identifiers,
outcomes, timing and sanitized error codes. Raw input/output, exception messages,
native payloads and arbitrary stored fields are excluded. Malformed persisted
projections and counter inconsistencies reject without repair or raw diagnostics.
This is a diagnostic record, not authenticated execution evidence or an atomic
business audit: a crash can occur between state/effect commit and event emission.
No exactly-once business effect or native resumability claim follows from it.

Local SQLite/PostgreSQL checks pass 15 cases, including real producer kill,
independent writers, exact retained timestamps/IDs, privacy, corruption, replay
gaps and byte/count bounds. Four actual ADK/LangGraph × SQLite/PostgreSQL
restart cases pass with one HTTP dispatch, original retained events and no model
calls. A real API restart test verifies denial without an events-read grant,
allowed retained snapshot/replay, durable capability reporting and unknown-cursor
409. The API/authorization/history selection passes 79 cases before the additional
API witness; all three final streaming API tests pass. A separate existing
observability/streaming/metrics/configuration selection passes 81 cases.
Reports are retained under `tmp/javascript-sandbox-20261025/`.

This candidate still needs Linux/native and coherent full-source qualification.
Trace-context/causation propagation, richer authenticated provenance and broader
replay diagnostics remain open. No S4-05 task or inventory row is promoted.
