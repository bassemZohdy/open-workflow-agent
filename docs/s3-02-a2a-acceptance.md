# S3-02 finite A2A invocation and interoperability acceptance

Status: implementation in progress; S3-02 remains unchecked. S2-09's finite
recovery policy and S3-01's finite Python MCP profile are verified, with S3-01
published on PR #65 at exact source `af4acd61c200787fb68248b2d180ba69efb8c837`.
Those results satisfy the prior-task prerequisites only; no A2A SDK, delivery,
streaming or native/backend profile is qualified until its own integrated gates
and exact-source matrix pass.

## Contract and authority

Open Workflow 1.0.3 remains the public DSL; its official schema is unchanged.
Every invocation executes a workflow, including the generated one-task default
agent workflow. Workflow A2A calls and configured native agent tools use one
typed common service. SDK 1.2.2 is the selected review/pinning candidate for
stable release 1.0.1 and wire 1.0; package installation does not qualify a method,
binding, discovery flow or native interoperability.

A2A Tasks project common invocation state. Task ID is the invocation ID; context
ID remains the common session identity. Common ownership, original-definition
archive, receipts, deadlines, approvals and child cancellation stay authoritative.
No A2A checkpoint store, second execution engine or native state transplant is
introduced. Skills are deployment-owned workflow bindings. A caller cannot
select arbitrary files, catalogs, engines, schemas or sandbox targets.

Inbound support remains optional and disabled by default. JSON-RPC and HTTP+JSON
are the finite target bindings; broader transports require separate admission
and acceptance. Required message/Part/role/ID/context fields and finite bounds
must validate before creating or resuming an invocation. Known null and
structured artifacts remain distinct from absent output. Cross-principal and
unknown Task access remain indistinguishable. Terminal retrieval is read-only.

Async acknowledgment follows durable admission of the accepted initial
delivery. Existing-task returnImmediately and streaming input resume are
explicitly unsupported in this finite profile; blocking protected resume remains
available. Durable resume consumption needs a separate authoritative wait-revision
gate and is not inferred from the initial queue. Original input and resume input
remain separate. A private
delivery record may retain immutable request/source/skill/principal authority
and bounded claim state; dispatch uses the existing common initial/resume path
and execution fencing. An acknowledged pre-native initial delivery does not
authorize public resume to invent a missing checkpoint. Host drain stops local
workers without converting observation or shutdown into business cancellation.

Outgoing card/interface selection and method models must use reviewed official
semantics and the common bounded HTTP/authentication service. Discovery does not
grant credential forwarding to a different origin. Exact response correlation,
known remote errors and dispatch phase determine whether the common action has
a known result or remains pending uncertainty. Message ID and operation headers
do not establish universal peer deduplication. No automatic resend of a possibly
accepted SendMessage, hidden page fetch, indefinite reconnect or remote rollback
is authorized.

Streaming, when advertised, is a bounded protocol projection with exact event
alternatives, task/context correlation, total deadline, byte/event/queue limits
and cleanup. Cross-worker completion must observe authoritative common state;
a process-local subscriber alone is insufficient. Disconnecting an observer
does not cancel execution. Timer/child progress must be distinguished from
approval or other input-required waits using authoritative common evidence.

## Required finite gates

| ID | Required evidence |
| --- | --- |
| A2A01 | Official SDK client→OWA and OWA common client→official SDK peer on actual loopback sockets for every advertised binding/method. Reviewed card/interface/version precedence, typed parameters/results/errors, exact IDs, modern shapes and rejected legacy or unsupported installs. |
| A2A02 | Identical actual ADK/LangGraph workflow calls and configured native agent-tool invocation through the common service, deterministic provider/tool choices and generated default workflow. Direct registry calls alone do not qualify native tool use. |
| A2A03 | Synchronous and returnImmediately completion, protected input-required resume, get/cancel, null/structured artifacts, deployed skill policy and context/owner checks. Malformed or mismatched input rejects before invocation/input/receiver mutation; unknown and cross-principal errors match. |
| A2A04 | Real timer/child/approval waits and authoritative cancellation/deadline/END projection. Observer disconnect does not cancel; first terminal outcomes and uncertain external actions survive late native results and host drain. |
| A2A05 | Actual producer PID stop after async ACK before native setup, after claim before dispatch, and after remote acceptance before receipt/native completion; separate recovery PID on SQLite and actual PostgreSQL. Original invocation/session/principal/source/input/skill/action identity, one remote task and bounded model/action counts remain. Initial delivery competing workers and stale generations are fenced. Async resume delivery remains unsupported until its separate durable consumption gate qualifies. |
| A2A06 | Existing Tasks resume archived original v1 under a v2 deployment while current skill authority still applies. Missing/corrupt archive/native/receipt/owner evidence rejects before unsafe lease/native/receiver writes. Terminal retrieval remains immutable and network-free. |
| A2A07 | Actual TLS and named bearer/API-key/finite OAuth/mTLS controls. Card-selected origin, redirect, resource/audience and credential policy rejects before preparation/business send when unauthorized. No resolved secrets in card/Task/capabilities/errors/logs/ordinary receipts. Existing inbound bearer scope is preserved; no delegated identity forwarding. |
| A2A08 | Bounded streaming/resubscription, initial Task and ordered status/artifact alternatives, cross-worker final state, expiry/backpressure/size/malformed/lost-response cleanup and precise optional-method rejection. Push, delegated grants and unqualified transports cannot become capabilities from SDK availability. |

The unchanged official OWS 1.0.3 inline A2A schema includes the seven supported
methods, including `tasks/list` and `agent/getAuthenticatedExtendedCard`.
Its `parameters` property is optional, but an object must contain at least one
property. Empty SDK parameters therefore omit the DSL property rather than
supplying `{}`. The shared native grid retains seven inline methods and one
configured-tool call; neither internal normalization nor the SDK expands the
public schema or enables its separately gated push operations.

The authored inventory currently includes common initial-delivery controls
(26 SQLite/PostgreSQL nodes), actual native workflow/tool calls (64), official
SDK inbound socket cases (16), actual process-stop initial delivery (24), and
inbound field/context/cross-worker/SDK availability controls (22). These are
declared counts, not executed evidence. Outbound common SDK HTTP/card/credential controls add 84 declared nodes;
immutable card admission adds 53 declared nodes (26 for each backend and
one official SDK portable-workflow case). Common native SQLite inspection
requires its 14 Windows/Linux controls. The process grid covers ACK before SDK setup, persisted native
projection, and known business receipt before native commit, plus revoked
skill/archive/queue corruption rejection. The initial delivery preserves v1
source/input/identity/clock under v2 and has no paid model calls.

Exact source provenance and passing counts require the completed validation
batch. SDK version 1.2.2 is required before enabled inbound exposure; sanitized
capabilities retain configured enabled separately from effective SDK availability
and do not claim full conformance. Finish the batch before behavioral tests; then run focused
actual SDK/common/native/database cases and existing A2A/protocol/security/CTK
regressions before complete independent suites, builds and hosted gates.
Mandatory actual principal/backend cases cannot qualify through foreign SDK or
database skips. Retain failed attempts and finite supervisor/source hashes.

## Independent gates and publication

Push callbacks remain S4-10 delivery/security work. Delegated identity, token
exchange and consent remain S3-06/S4-11. gRPC remains S3-03 unless separately
reviewed and qualified. JavaScript/TypeScript and full T67 interoperability stay
S4-03/S4-04; Python/native evidence cannot close them. T32/T33 receive only
explicitly proven finite evidence. Full A2A/OWS conformance, arbitrary version
migration, remote rollback and general exactly-once guarantees are not claimed.

Retain predecessor LC184, VM68, MCP64, common security/platform controls, seven
audits, two native benchmarks, 26 canonical CTK feature hashes and four runtime
image gates. All six hosted workflows must qualify the final combined source.
Update TODO/PROJECT and only proven tracker/audit subsets after acceptance;
proposed inventory tests and independent gaps remain visible and unverified.

The S3-02 implementation baseline was authored from
`325458af130cccc8d9d899dc09614f6e55cc48f5`; that provenance is not A2A
acceptance. With S2-09 and S3-01 prerequisites now verified, S3-02 is
actionable. Continue with the grouped peer/native/backend gates, preserve the
bounded semantics above, then run the frozen focused and full validation
profiles. No blocker is recorded.


## Retained first combined batch

Frozen source `ad441432` remained unchanged throughout the first batch. Root
reported 12 failed / 203 passed / 26 skipped; actual ADK reported 12 failed /
28 passed / 75 skipped; actual LangGraph reported 12 failed / 28 passed /
76 skipped; optional Agent Framework reported 81 passed / 316 skipped. Five
independent wheel/sdist builds passed. These results are retained under
`tmp/s3-02-first-batch` and do not qualify the source or any skipped principal.

Failures exposed fixture setup mismatches (invented configuration field,
nonexistent child dispatcher start hook, incomplete modern messages, empty
optional parameters) and stale typed-error/structured-Task assertions. They also
exposed a real terminal queue settlement early-return defect, now corrected.
The correction retains owner/input/identity assertions and adds stop-window
coverage within the existing counts. The full corrected batch remains pending;
S3-02 and its feature criteria stay unchecked and unqualified.


## Retained second combined batch

Frozen source `296c60e` remained unchanged throughout the second batch. Root
reported 2 failed / 424 passed / 79 skipped; actual ADK and actual LangGraph
each reported 13 failed / 112 passed / 232 skipped; optional Agent Framework
reported 81 passed / 316 skipped. Five independent wheel/sdist builds passed.
Full proof is retained under `tmp/s3-02-second-batch`; no skipped SDK/backend
profile or successful build substitutes for runtime qualification.

The remaining failures identified a real authenticated AgentCard compatibility
defect (legacy bearer security fields), an incorrect SDK subscription method in
the peer fixture, shared-helper import placement, and older version-recovery
fixtures with incomplete modern requests or stale artifact assertions. The
correction publishes strict SDK-parsed v1 `httpAuthSecurityScheme` and
`securityRequirements` while retaining the bearer scheme identifier and excluding
secrets. The existing positive card test now parses the complete response through
the official SDK. The stream fixture uses its actual `subscribe` SPI. Counts
remain unchanged; the complete correction batch and full qualification remain
pending, with S3-02 and its feature criteria unchecked.

## Retained third correction focus

Frozen source `386b390` remained unchanged throughout the third focused batch.
Root reported 285 passed / 39 skipped; actual ADK reported 2 failed / 39 passed /
123 skipped; actual LangGraph reported 3 failed / 38 passed / 123 skipped. Five
independent wheel/sdist builds passed. Exact logs and source snapshots are retained
under `tmp/s3-02-third-batch`. These focused results do not qualify skipped profiles
or substitute for the required full/native/backend/hosted acceptance.

The actual SDK streaming peer exposed a runtime wire defect: JSON-RPC SSE data
contained bare streamed results instead of correlated `{jsonrpc,id,result}`
envelopes. The correction wraps every frame before bounded byte accounting and
forwards the validated original request ID; HTTP+JSON keeps bare streamed results.
Existing common fixtures now validate the exact envelope fields, version and ID
type/value before inspecting any result, including live ASGI and disconnect readers.
The remaining producer fixtures identified a worker receipt-serialization mismatch
and the selected LangGraph partial-native stop boundary. Their correction retains
the same required identities, outcomes, native-history and no-repeat-dispatch
assertions. Fixture counts remain unchanged. S3-02 and its criteria remain unchecked;
the complete corrected batch and final qualification are pending.

The fourth frozen correction at `0c5eec0` passed 55 common wire controls
(13 backend skips), all 10 actual SDK/socket/process-stop cases per principal
(30 foreign/backend skips each), and all five independent builds. The 13 new
LangGraph initializer corruption/control cases failed at fixture creation because
a required `user_id` was omitted, before archive or SDK setup; that fixture field
is corrected without changing runtime code or assertions. Exact failed and passing
history is retained in `tmp/s3-02-fourth-batch`. Final full suites, actual PostgreSQL,
platform, image, security, CTK and exact-source hosted acceptance remain required.

### Bounded full-suite history at 072fa41 (not qualification)

Four independent local full suites retained an unchanged 433-input source
snapshot and continued making progress until their explicit 3,000-second
Windows bound. Each exited124 with timed_out=true; none is a passing full
suite or coverage gate. Five standalone/root/engine builds passed on that source.
The original full logs, start/result records and source manifest are retained in
`tmp/s3-02-full-bounded-072fa41`; no timeout or earlier failure is promoted.

The catalog mock did not provide the pinned SDK's correlated JSON-RPC2.0 typed
Task result. Its correction retains the inline/imported translation comparison,
adds exact request version and unique IDs, echoes the request ID and returns the
typed Task directly. Final LangGraph log review also exposed the common HTTP
regression in preexisting recovery fixtures: preparing the client's own named
bearer binding discarded its explicit verified private CA. Positive preparation
failed TLS before native-corruption controls could execute. A bounded standalone
reproduction and saved producer/query-only artifacts confirmed that shared cause.

The grouped correction preserves a client's selected CA only for its inherited
or exact same authentication object, while an explicitly different selected
profile still replaces global trust and client identity. Six new direct/pinned
real TLS witnesses cover retained bearer trust and rejection of foreign-profile
CA inheritance. The common TLS suite now declares10 cases. Windows native routing
uses verbose rows so all13 initializer controls can be independently audited.
Corrected scoped checks, fresh builds and all exact-source hosted full-suite,
coverage, PostgreSQL, Windows, image, audit, CTK and benchmark gates remain
mandatory before S2-09/S3-01/S3-02 closure or publication qualification.

### Grouped trust correction at 39b1024 (scoped history only)

The corrected real TLS grid passes all10 cases in independent common/principal
and optional Framework environments. Actual ADK and LangGraph each pass35
selected checks (69 explicit foreign/backend skips), including every applicable
11 native corruption and12 OS-stop recovery case that previously failed before
TLS admission. Optional Framework passes13 selected cases. Five fresh builds
pass, and every supervisor records zero source changes. These scoped passes do
not turn the earlier bounded full suites into green full/coverage evidence.

The common root check retains411 passes/26 skips and one timing-dependent MCP
fixture failure. Its original30ms whole-operation budget expired before the
shared dispatch marker, so the runtime correctly returned a known pre-send
ToolError. The fixture now keeps a finite1s original budget, explicitly waits
for response-body entry, and then requires post-send uncertainty, exactly one
request and actual stream closure. The body remains indefinitely stalled; no
clock is reset and no runtime classification or retry rule is relaxed. Final
fixture checks, source-matched builds and hosted full acceptance remain pending.
Logs and immutable source/result records are retained in
`tmp/s3-02-postfull-39b1024`; earlier failure history remains distinct.

### Transport fixture phase review at e63d096 (not a green root batch)

Independent ADK/LangGraph fixture checks pass98 cases each and optional
Framework passes99; all common TLS10 and MCP86 witnesses execute. Five builds
pass on that source with zero changed inputs. Root retains411 passes/26 skips
and one60ms issuer/resource phase assertion failure: issuer handling consumed
the budget before any resource request, which is a valid pre-send timeout.
The saved root log is retained in `tmp/s3-02-final-fixture-e63d096`.

The grouped fixture correction uses explicit issuer/resource/body entry events
and modest finite original budgets, with permanently stalled mocked receivers.
The combined issuer/resource witness additionally inspects the actual resource
request's remaining read timeout after a known issuer delay; resetting a new
full resource budget still fails. Post-send MCP/A2A witnesses require one
actual dispatch and stream cleanup. Metadata rejection fixtures no longer rely
on a sub-scheduler-sized timeout unrelated to their admission purpose. No
runtime deadline/classification or known/uncertain replay semantics changed.
Full hosted qualification and final common fixture validation remain required.

### Hosted f020d6e failure review and grouped corrections (unqualified)

The final scoped common checks passed237 root,228 ADK,228 LangGraph and229
optional Framework cases, and five fresh package builds passed with unchanged
source inputs. The pushed checkpoint passed Docs, Security, external sandbox,
both standard runtime images, and both Linux native lifecycle selections except
the four LangGraph SDK inbound PostgreSQL-labeled cases. These scoped results do
not qualify the failed aggregate workflows or close any pending task.

PR CI root and principal shared suites exhausted their eight-minute commands at
22%,46% and51%. Optional Framework exhausted its ten-minute job at36%; its
PostgreSQL selection exhausted five minutes before the final OS-stop case.
Windows timing exceeded its job budget and also retained two deadline recovery
failures; Windows ownership retained one first-invoke waiting timeout. PostgreSQL
common retained29 failures. Principal PostgreSQL recovery selections retained
assertion failures before their14-minute bounds; their aborts omit final failure
stacks and cannot establish exclusive causes. Original logs and artifacts are
retained in the primary checkout's `tmp/ci-f020d6e-*` evidence.

The grouped fixture corrections isolate content archives and retire only
fixture-owned queues/schedules after negative snapshots, supply genuine leased
original parent/child admissions before pending effects, observe archive-first
timer recovery rather than expecting network compilation, and suppress global
timer polling only in fixtures proving explicit invoke/resume boundaries.
Automatic-delivery acceptance retains real pollers. The child deadline OS worker
now reopens the original32/1000 deployment quotas; its former64/100000 recovery
policy was correctly rejected by the retained profile. Separate common controls
require changed quotas to fail without mutation. Worker failures retain common
reason details. SDK inbound PostgreSQL selection now uses its actual configured
database and asserts the common/native storage destination before network I/O.

Finite aggregate CI budgets now cover the measured complete selections while
preserving every fixture, original per-case deadline, independent backend/job,
90% root coverage, image/audit, CTK and benchmark gate. These edits still require
one frozen grouped behavioral run and fresh complete hosted qualification. No
cancelled, timed-out, mislabeled-backend or earlier-source result is counted as
acceptance, and no task is deferred while actionable corrections remain.
