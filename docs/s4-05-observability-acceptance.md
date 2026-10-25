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

Frozen source `352034921fdb9688cc19ccf005c7bd31b9c82e97` now also passes the
Linux candidate selection: 45 ADK cases (11 explicit skips) and 137 LangGraph
cases (15 explicit skips), including all 15 common history cases, each selected
SDK's two database restart cases and the three API tests. Both freshly rebuilt
production images pass the JavaScript packaged gate; all 1,145 frozen inputs
remain byte-identical. This is focused Linux evidence, not the pending complete
hosted gate. Reports are retained in `tmp/javascript-sandbox-20261025/3520349/`.

The later corruption/error suite passes 43 common SQLite/PostgreSQL cases.
It checks counter damage, invalid envelope/schema/type fields, unexpected error
or progress data, SSE ID injection, storage loss, invalid writes and closed
observers without leaking raw diagnostics or repairing evidence. The
SQLite/streaming-API coverage selection passes 25 cases with 21 unconfigured
PostgreSQL skips and covers 97.11% of the new history module. Its initial focused
coverage result was 84.39%; meaningful error-path tests close that gap without
lowering the existing root coverage gate. These later tests are separate from
the frozen `3520349` selection and still need the next Linux/full-source batch.

## Bounded tracing, correlation and provenance candidate — 2026-10-25

`observability.tracing` now selects a framework-neutral in-process profile,
`owa/execution-tracing/v1`. It instruments owned invocation segments and common
task scopes after the existing authorization and ownership fences. Defaults are
disabled, 1,000 retained spans, 1,024 active spans and 4 MiB serialized retained
records. Disabled or saturated scopes clear their active context and restore the
previous context on exit. Count/byte eviction and dropped-span counters make
diagnostic loss observable without changing business execution. No SDK, broker,
collector, paid API or startup installation is added.

Immutable records contain generated trace/span IDs, common invocation and
workflow fingerprint, canonical task reference, parent invocation trace link,
UTC start time, monotonic duration and bounded outcome/status. Invocation IDs
deterministically correlate separate recovery segments; span IDs are fresh and
nested tasks retain their parent span. Child invocations use a distinct trace
with a common parent-invocation link. No original input/output, prompt, URL,
credential, native reference/state or exception message is accepted. Metrics
keep their existing low-cardinality labels; trace IDs are never metric labels.

The wire format uses generated version-00 contexts from the pinned
[W3C Trace Context Level 1 recommendation](https://www.w3.org/TR/2021/REC-trace-context-1-20211123/).
The optional lifecycle `traceparent` follows the
[CloudEvents distributed tracing extension v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/extensions/distributed-tracing.md).
History validates the exact nonzero lowercase version-00 representation before
writing and retains original contexts during snapshot/SSE replay. This is a
generated-context subset: incoming headers, tracestate, baggage, vendor
propagation, OpenTelemetry semantic conventions/exporters and durable span
storage are not implemented. HTTP propagation is a second independent default-off
setting; the common HTTP client adds context only when no caller traceparent
header is present. No trace field is an identity or permission source.

The existing object-authorized recovery diagnostic exposes the common stable
trace link when enabled. Compatible definition inspection also projects only
the verified original definition/runtime profile digests, after existing stored
evidence, admission, current trust and authority checks. Failed evidence never
produces these provenance digests. Diagnostic inspection remains read-only,
bounded, and explicit that native state/readiness is not inspected. No history
or trace record authorizes resume or constitutes an atomic business audit.

Working-tree tests exercise nesting, separate segments/child links, concurrency,
fault/cancel cleanup, normal-return fault/cancel outcomes, disabled-policy
isolation, malformed contexts, count/byte/active budgets, oversized reference
omission, HTTP opt-in/caller headers, history retention/replay and authorized API
restart. Both actual Python SDKs pass SQLite/PostgreSQL history/recovery cases
with one HTTP dispatch and one stable trace ID. Physical history producer-kill
checks retain the original context. Full-source, Linux and current candidate
qualification remain required; no task or research criterion is promoted here.

The focused Windows candidate selection passes 137 cases with 59 explicitly
unconfigured/opposite-profile skips and statement coverage of the tracing module
at 100% (132 statements). The later complete streaming API file passes five
cases, including incoming-header rejection as a context source. A separate
CI/metrics/configuration/observability/stream selection passes 294 cases.
Actual ADK history/native recovery plus common history passes 51 cases across
SQLite/PostgreSQL with two opposite-SDK skips; actual LangGraph passes both
native database cases with two opposite-SDK skips. These are working-tree
results, not a substitute for exact-source Linux/hosted qualification.
