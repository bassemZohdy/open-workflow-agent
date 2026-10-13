# S3-02 finite A2A invocation and interoperability acceptance

Status: implementation in progress; S3-02 remains unchecked. This record defines
required evidence and exclusions. No newly authored SDK, delivery or streaming
profile is qualified before integrated validation. S2-09 and S3-01 qualification
remain prerequisites for publication of the combined behavior.

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

Authoring starts in isolated `codex/a2a-integrations` from source
325458af130cccc8d9d899dc09614f6e55cc48f5 while its predecessor frozen suites run.
That source is an implementation baseline, not successful S2-09/S3-01 or new
A2A evidence. There is no established blocker: the identified gaps have concrete
implementation steps, with publication waiting on the stated dependency gates.


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
