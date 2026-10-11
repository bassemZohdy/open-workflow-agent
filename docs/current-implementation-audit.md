# Current implementation audit

S1-08 compares every tracked feature with active admission/execution code and
the scope of actual test assertions. The machine-readable
[implementation audit](../resources/compatibility/implementation-audit.json)
contains all **658 feature IDs** and 78 evidence groups. Each entry preserves
its original construct, proposed tests, independent Python/JavaScript outcome,
remaining scope and milestone owners. Source symbols and test functions are
linked explicitly; no predecessor reference file serves as runtime proof.

The reviewed implementation is
`58516fb`, which retains the verified fork, original-input, terminal-record,
leaf-call and conditional common write corrections. Completed outcomes are
reused before native task commit; changed requests and uncertain intents stop
recovery without repeat dispatch or ordinary task/catch retries. Uncertain
competitive actions cannot establish a winner. Stale writes cannot replace newer
records or terminal outcomes, and stale state conflicts stop retry/fork routing.
Fresh recovery now reads an existing original-input binding without inserting
resume data. Principal native adapters validate initial ADK invocation/root
LangGraph checkpoint input before a status transition. Missing/deleted native
records, missing/changed original input and unavailable installed native
persistence reject admission. ADK invocations sharing a session retain distinct
original inputs. Admission storage/cleanup errors are sanitized.
Full local suites passed; exact new-source GitHub/database acceptance is pending.
The conditional-write gates passed at `6954db8`; prior leaf-call and corrected
dependency gates passed at `fc53574`. Recovery, action idempotency and lifecycle
remain partial. S2-03 remains open for broader effects, fenced ownership and
full native checkpoint consistency. Initial input-binding checks do not
establish nested checkpoint validation or atomic native write fences.

Earlier competitive runtime, fixture/audit and all three common PostgreSQL
gates passed at `2fbe548`, `51c204e` and `81d27b3`. The JSON null wake-up fix also
passed every exact gate at `f2943c6`. Original-input and terminal recovery passed
all exact gates at `6840605`; private journal storage passed at `d197fa1`.
See [competitive acceptance](../PROJECT.md#s2-02-competitive-fork-decisions--2026-10-11-verified)
and [recovery progress](../PROJECT.md#s2-03-checkpoint-recovery--2026-10-11-in-progress).
Hashes of reviewed Git file contents preserve the evidence baseline. The
manifest's `previous_reviews` retains the earlier S1-08 acceptance and points
to its immutable artifact. Historical green gates do not qualify changed code.

Historical successful Security jobs used a parser that missed nested advisory
records and therefore do not prove absence of fixable dependency advisories.
The source-scoped behavioral results remain independently recorded. See the
[dependency audit correction](../PROJECT.md#dependency-audit-correction--2026-10-11-verified).

## Outcomes

| Python audit outcome | Features | Meaning |
| --- | ---: | --- |
| Qualified subset | 181 | Specific bounded behavior has tests; full inventory criteria remain unverified |
| Partial | 150 | Some requested behavior exists, with explicit missing scope |
| Behavior mismatch | 32 | Accepted behavior differs from the tracked construct |
| Rejected | 197 | Admission/client guard rejects the request; this is failure evidence, not implemented support |
| Inert | 27 | Field is accepted/preserved without its requested effect |
| Internal only | 9 | Native primitive is used privately, without a general public capability |
| Unimplemented | 62 | No public implementation found in the reviewed paths |

These counts include both semantic capabilities and individual schema fields.
Fields behind the same unsupported parent inherit that parent's rejection;
they do not constitute 197 separate exercised implementations. The audit
groups repeated schema fields by their actual parent construct, rather than
their category label. For example, `AS305` is an extension selector for switch,
not proof of ordinary switch support; `AS137` is MCP client name, not a generic
integration name.

**No full feature is certified complete by this audit.** All 443 proposed
acceptance tests retain their unexecuted full-criteria status; existing fixtures
may prove selected subsets without satisfying a proposed test's entire
backend/restart/operational contract. The 774 schema constraints remain a
structural inventory, separate from runtime proof. Independent JavaScript/
TypeScript implementation and qualification are absent across the inventory.
Original source/API inventory statuses are retained for provenance and must
not be read as current delivery status.

## Findings that change the remaining work

| Finding | Actual behavior and evidence boundary | Repair owner |
| --- | --- | --- |
| Ordered fork output and collisions | Ordered branch arrays, isolated exports, explicit jq aggregation and failure cleanup pass shared fixtures. SQLite/PostgreSQL native fork restart and exact runtime GitHub gates pass; additional shared output restart passes every adapter | S2-01 verified; full criteria remain S4-03/S4-04 |
| Competitive fork durability | First observed completion, cancellation intents and distinct occurrence identity are persisted. SQLite/PostgreSQL replay preserves values/null/fault/end and repeated occurrences without branch calls on both principal adapters and the optional common bridge. All exact acceptance gates passed. External-action reconciliation/compensation is not established | S2-02 verified; general effects S2-03/S2-08; full criteria S4-03/S4-04 |
| Native checkpoint recovery | Original input, resume values, atomic terminal outcomes and leaf-call receipts have bounded SQLite/PostgreSQL acceptance. Conditional common writes retain newer records against stale workers; conflicts stop retry or a fabricated fork winner. New-source database gates are pending. Exclusive execution ownership, native checkpoint fencing/consistency, broader effects and legacy migration remain open | S2-03 in progress; migration S2-09 |
| Action idempotency and compensation | A080 is partial: selected call receipts prevent completed replay and block uncertain actions. A079 remains unimplemented: no compensation/saga state machine exists. Run/event/child effects, redrive, ambiguous outcome resolution, full process-crash criteria and exactly-once external actions remain unqualified | S2-03, S2-08; full criteria S4-03/S4-04 |
| Retry and timeout declarations | Predicates/count/basic backoff work; jitter and duration limits are inert, and original deadlines are not persisted | S2-04 |
| Events and timers | Active process-local listeners and persisted after/every schedules exist; durable general inbox/outbox/timers/correlation/cron/on are missing. Explicit null event data is omitted | S2-05, S2-06 |
| Child workflow recovery | Synchronous child invocation/parent identity exists; recursive budgets, asynchronous children, durable cancellation and compatible version migration remain missing | S2-07, S2-09 |
| MCP | Seven bounded HTTP methods exist; full official SDK sessions/transports/notifications are not qualified, and options/client description are inert | S3-01 |
| A2A | Inline/imported OWS names translate to current wire names. Outbound ListTasks and GetExtendedAgentCard exist; outbound streaming/push remain unavailable despite bounded inbound streaming. AgentCard URLs are not resolved as descriptions | S3-02; push remains conditional |
| OpenAPI and HTTP raw | OpenAPI posts to the description URL instead of resolving operation/server/path. HTTP raw returns decoded text rather than base64, losing invalid UTF-8 bytes | S3-03 |
| Executable operations | All operations use the common SandboxManager. Container lifetime/pullPolicy fields are inert; external script source, unsafe mappings and background/return modes are rejected | S3-04 |
| Protected human work | Durable approvals exist; reassignment/escalation and full native interrupt semantics remain missing | S3-05 |
| Security | Deployment profiles/principal access are separate from rejected workflow authentication/secrets. OAuth2 token retrieval lacks a response-size bound; full credential/delegation propagation remains unqualified | S3-06; RFC 8693/consent remains conditional |
| Lifecycle vocabulary/history | Current CloudEvents use `com.openworkflow.agent.lifecycle.*`, not the inventory's `io.serverlessworkflow.*`; durable history/correlation/replay remains incomplete | S4-05 |
| Native features and JS | Private StateGraph nodes/channels/savers do not establish public channels/reducers/Command/Send/Store/stream/history/replay APIs. No independent JS frontend exists | S4-01 through S4-04 |

`$workflow.startedAt` and `$task.startedAt` contain tested DateTime components.
There is no global `$dateTime` binding. The audit preserves the inventory's
global-looking construct and flags that interpretation explicitly rather than
claiming a global descriptor from component-shape tests.

## Verification of the audit

[Integrity checks](../tests/ci/test_implementation_audit.py) verify exhaustive IDs,
active source/test symbols, ownership, protocol-parent distinctions and retained
unexecuted criteria. Those checks validate the evidence manifest, not runtime
conformance. Existing bounded fixtures remain linked in the manifest to their
exact reviewed implementation acceptance.

[Gap probes](../tests/contract/test_implementation_audit_gaps.py) assert desired
fork, OpenAPI, HTTP raw and null-event behavior. Ordered-fork checks now pass
without expected-failure markers: the root and principal engine environments
report eight passed and three expected assertion failures; optional Agent
Framework reports nine passed and three expected assertion failures. The
remaining failures concern OpenAPI routing, HTTP raw binary data and explicit
null event data. Prior unmarked runs confirmed the relevant output/request
assertions. Their strict, AssertionError-only xfails keep unrelated
errors failing CI and require marker removal when a repair makes the desired
behavior pass. Remaining expected failures are open defects. The passing fork
probes prove their actual subset, without closing complete inventory criteria.

The backlog and tracker retain earlier verified milestone scopes. The audit
adds missing repair ownership, including S2-01 for state collisions that were
previously mapped only to completed expression/data milestones. Conditional
backlog references do not activate optional Agent Framework hardening, external
brokers, push notifications or delegated identity work.
