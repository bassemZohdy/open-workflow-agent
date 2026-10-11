# Current implementation audit

S1-08 compares every tracked feature with active admission/execution code and
the scope of actual test assertions. The machine-readable
[implementation audit](../resources/compatibility/implementation-audit.json)
contains all **658 feature IDs** and 78 evidence groups. Each entry preserves
its original construct, proposed tests, independent Python/JavaScript outcome,
remaining scope and milestone owners. Source symbols and test functions are
linked explicitly; no predecessor reference file serves as runtime proof.

The reviewed implementation is
`e61c6a5`, which retains the verified fork, original-input, terminal-record,
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
Initial native input admission passed all seven gates at `b9a2b31`.
Separate test-only proof at `68722d3` passed eight actual forced OS process-stop
cases per principal SDK and optional common bridge on SQLite. Recovery in a
distinct process preserves first outcomes and existing receipts without repeating
fake external dispatch. Optional Agent Framework native durability remains
unqualified. Configured PostgreSQL and Linux/Windows gates subsequently passed
at `ef29f23`. These focused results do
not replace the full-suite baseline recorded at `58516fb`.
The private ownership storage foundation passed all seven gates at `cdd6e17`.
Runtime source `b2dfc5c` now guards invocation/resume admission and actual
common/native checkpoint transactions, with renewal, loss and shutdown draining.
Both principal SDKs retain their native formats, SQL and checkpoint ownership.
The same shared fixtures pass on configured PostgreSQL; both PostgreSQL image
jobs pass. Linux root passes 1,536 cases (300 skips, three gaps, 91.87%);
Windows root/native suites and principal native/CTK suites pass. Six fresh
dependency reports contain zero known advisories. Both main CI image restart
gates fail immediate resume with a live killed-owner lease; their ADK image
jobs are cancelled. At `2acf632`, production-expiry-aware image acceptance and
action/fork admission guards pass all seven exact gates, including both main CI
forced-stop images and configured PostgreSQL. Six actual dependency reports
contain zero known advisories. Source `556da47` adds actual LangGraph root and
nested snapshot validation before common resume admission. Two prior corruption
probes fail; 24 local SQLite clean/corrupt cases and the 809-case full native,
shared and CTK regression pass, with 254 skips and three known gaps. Its fresh
GitHub acceptance at `fc54600` passes six gates and both main CI forced-stop
images. PostgreSQL fails all 22 corruption mutations before recovery because
the fixture targets blob rows instead of SDK-inline primitive JSONB channels;
these failures do not qualify corruption rejection. Source `005ab5b` corrects
the fixture and re-reads the exact altered checkpoint. It also rejects ADK
contradictory later input events and malformed/duplicate-key native input JSON,
while accepting matching repeats and preserving shared-session identity.
Focused checks pass; the full ADK native/shared/CTK suite passes 780 with 276
skips and three gaps. All seven exact `4058895` gates pass, including 127
LangGraph and 108 ADK configured recovery cases and both PostgreSQL/main CI
forced-stop images. Source `e61c6a5` additionally rejects Boolean terminal
controls that disagree with task routing, after six actual prior failures.
Its full local LangGraph regression passes 815 (270 skips, three gaps); fresh
configured acceptance is pending. Actual SDK probes still accept missing or
changed common receipts after a native-completed call, then dispatch the next
call and change invocation state. Receipt dependency reconciliation remains open.
Full local frozen
results and the final
PostgreSQL-only ADK adjustment retain their exact scopes in the manifest.
The conditional-write gates passed at `6954db8`; prior leaf-call and corrected
dependency gates passed at `fc53574`. Recovery, action idempotency and lifecycle
remain partial. S2-03 remains open for broader effects and full native checkpoint
consistency. Selected LangGraph snapshot checks do not establish all ADK private
state, missing-cursor or well-typed native/common receipt reconciliation. No full feature outcome
or independent JavaScript qualification is promoted by this bounded proof.

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
| Native checkpoint recovery | Original input, resume values, terminal outcomes and leaf-call receipts have bounded SQLite/PostgreSQL acceptance. Runtime admission, common/native transaction fencing and action/fork guards pass native, Windows and PostgreSQL fixtures; both main CI images pass actual lease-expiry recovery. Selected root/nested LangGraph snapshot checks pass local regression and await configured acceptance. Full native/common consistency, broader effects and legacy migration remain open | S2-03 in progress; migration S2-09 |
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
