# Current implementation audit

S1-08 compares every tracked feature with active admission/execution code and
the scope of actual test assertions. The machine-readable
[implementation audit](../resources/compatibility/implementation-audit.json)
contains all **658 feature IDs** and 78 evidence groups. Each entry preserves
its original construct, proposed tests, independent Python/JavaScript outcome,
remaining scope and milestone owners. Source symbols and test functions are
linked explicitly; no predecessor reference file serves as runtime proof.

S2-05 embedded event storage, ordered strategies, correlation and sequential
listener iterators are qualified subsets at publication `58de90e`, with runtime
execution bytes at `143d38a`. All four local suites and seven configured gates
pass; the updated admission, storage and native/process anchors reflect this
finite profile. Explicit JSON null event data now passes its desired-behavior
probe. The two remaining strict
expected failures retain their S3-03 owners. No full inventory/proposed or
independent JavaScript qualification is promoted by this progress update.

The reviewed implementation is `58de90e`, with S2-05 runtime/full-suite
provenance at `143d38a`; only its explicitly tested capability-publication
inputs differ. S2-04 remains qualified at `6f8e95d`, with earlier production
and full-local provenance at `a0ee9c0` preserved in the immutable audit history.
The source retains the verified fork, original-input, terminal-record,
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
configured acceptance was pending at that source. Historical SDK probes accept missing or
changed common receipts after a native-completed call, then dispatch the next
call and change invocation state. Receipt dependencies at `9755e96` subsequently
reject that disagreement on both principal SDKs; all exact `c276f40` gates pass.
Completed-scope dependencies at `968ec8f` pass local/Windows/native checks, but
the PostgreSQL gate at `ac478e1` deselects nine new scope cases. Successful jobs
do not qualify those missing cases. Source `3df61f6` corrects the actual selector,
adds real collection guards, and extends common receipts to existing sandbox
operations. Its focused principal suites pass; fresh full/configured acceptance
is recorded in the manifest and [PROJECT.md](../PROJECT.md).
Full local frozen
results and the final
PostgreSQL-only ADK adjustment retain their exact scopes in the manifest.
The conditional-write gates passed at `6954db8`; prior leaf-call and corrected
dependency gates passed at `fc53574`. Recovery, action idempotency and lifecycle
remain partial. S2-03 remains open for broader effects and full native checkpoint
consistency. Selected LangGraph snapshot checks do not establish all ADK private
state or complete native/common invocation/payload reconciliation. No full feature outcome
or independent JavaScript qualification is promoted by this bounded proof.

The corrected PostgreSQL gate at `c7b70ad` executes all nine completed-scope cases
and eighteen sandbox receipt cases per adapter. The shared recovery suites pass
186 LangGraph, 152 ADK and 110 optional Agent Framework checks, with seven
optional skips. Both PostgreSQL runtime images pass. This closes the earlier
test-selection gap for that source.

Source `5506277` records event publication acknowledgements and existing
synchronous child-call outcomes before native task progress. The focused suites
pass 70/64 in each principal SDK environment. Changed prepared input or resolved
child definitions reject replay; known outcomes are reused and uncertain intents
stop another dispatch. These receipts add neither durable event delivery nor
child scheduling/budget capabilities. At that source both principal SDK probes accept a saved
child result after deletion of the child's common invocation record. That
dependency gap motivated the subsequent reconciliation change.

Source `e39a7c0` binds the terminal child record and original input to the
prepared request. An action identity index is admitted atomically with intents;
pre-admission checks cover its receipts and descendant child actions even before
native task progress stores a receipt link. Missing or changed child records,
input, execution references, descendant receipts and index metadata now reject
recovery before parent mutation or dispatch. Both focused principal suites pass
77/74; storage/selection checks pass 52/9. The same deletion assertions fail on
exact archived `5506277` source. Full local and all seven configured gates pass
at acceptance artifact `ddd194e`; all ten scoped
[S2-03 requirements](s2-03-recovery-acceptance.md) are verified. Neither old child-receipt migration nor
general pending-intent resolution is implied.

Earlier competitive runtime, fixture/audit and all three common PostgreSQL
gates passed at `2fbe548`, `51c204e` and `81d27b3`. The JSON null wake-up fix also
passed every exact gate at `f2943c6`. Original-input and terminal recovery passed
all exact gates at `6840605`; private journal storage passed at `d197fa1`.
See [competitive acceptance](../PROJECT.md#s2-02-competitive-fork-decisions--2026-10-11-verified)
and [recovery progress](../PROJECT.md#s2-03-checkpoint-recovery--2026-10-12-verified).
Hashes of reviewed Git file contents preserve the evidence baseline. The
manifest's `previous_reviews` retains the earlier S1-08 acceptance and points
to its immutable artifact. Historical green gates do not qualify changed code.

Historical successful Security jobs used a parser that missed nested advisory
records and therefore do not prove absence of fixable dependency advisories.
The source-scoped behavioral results remain independently recorded. See the
[dependency audit correction](../PROJECT.md#dependency-audit-correction--2026-10-11-verified).

Source `23b9d35` starts S2-04: catch filters and catch/retry predicates now
read the configured error variable with structured fields and built-in status.
Known provider error codes survive task/journal boundaries and native replay;
unexpected exceptions remain sanitized. Local portable/recovery focus passes
95/29 per principal environment, with five actual provider-receipt replays each.
That error-context source passes all seven configured runs at `b6aef8c`.
Runtime timing source `f2b5c02` additionally passes jitter, duration and deadline
cleanup assertions locally: 85/39 timing/error/recovery and 93/26 lifecycle/fork
checks per principal environment. Eight previously inert timing fields are now
partial. That runtime source passes all seven configured gates at `cbb06b2`, including
eleven PostgreSQL timing cases per principal. Source `17d4197` adds atomic
original clock/task anchors with eight SQLite deadline cases per principal and
124/167 broader recovery checks per environment. Source `a0ee9c0` additionally completes saved retry decisions and scoped
exceptions locally, including actual process stops and all four frozen full
suites. Exact-source configured acceptance at `6f8e95d` verifies all thirteen
finite S2-04 requirements; the prior `fdd771e` fixture failures remain preserved. The earlier S2-03
acceptance at `ddd194e` does not qualify changed S2-04 code.

## Outcomes

| Python audit outcome | Features | Meaning |
| --- | ---: | --- |
| Qualified subset | 181 | Specific bounded behavior has tests; full inventory criteria remain unverified |
| Partial | 158 | Some requested behavior exists, with explicit missing scope |
| Behavior mismatch | 32 | Accepted behavior differs from the tracked construct |
| Rejected | 197 | Admission/client guard rejects the request; this is failure evidence, not implemented support |
| Inert | 19 | Field is accepted/preserved without its requested effect |
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
| Native checkpoint recovery | Invocation/input/terminal outcomes, SDK state/routing/scope consistency, action/child dependencies, atomic admission and ownership fencing pass identical SQLite/PostgreSQL assertions, forced process-stop, Windows and both actual runtime images. The finite recovery audit passes all configured gates at `ddd194e`; legacy/version migration and later full criteria retain their owners | S2-03 verified; migration S2-09; full criteria S4-03/S4-04 |
| Action idempotency and compensation | A080 is partial: selected call receipts prevent completed replay and block uncertain actions. A079 remains unimplemented: no compensation/saga state machine exists. Known sandbox/event/child outcomes and pre-admission descendant reconciliation pass S2-03. Redrive, ambiguous outcome resolution, compensation and exactly-once external actions remain unqualified | S2-08; full criteria S4-03/S4-04 |
| Retry and timeout declarations | Runtime count/backoff/jitter and attempt/aggregate budgets pass recorded-clock/RNG fixtures; timeout cleanup preserves uncertain intents. Original workflow/task deadline anchors pass native SQLite restart; retry clock/schedule/jitter and scoped exceptions pass full frozen local acceptance and all seven exact-source configured gates at `6f8e95d` | S2-04 |
| Events and timers | Embedded durable inbox/outbox, ordered strategies, correlation, explicit null preservation and sequential foreach pass full native/process/SQLite/PostgreSQL acceptance at `58de90e`. Durable timers and complete every/after/cron/event-trigger scheduling remain open | S2-05 verified; S2-06; external transports S4-10 |
| Child workflow recovery | Synchronous child invocation/parent identity exists; recursive budgets, asynchronous children and durable cancellation remain missing. S2-09's finite original-definition continuation and bounded recovery policy is verified; successful in-place version migration remains unsupported | S2-07; S2-09 verified finite policy |
| MCP | Seven bounded HTTP methods exist; full official SDK sessions/transports/notifications are not qualified. A locally tested correction explicitly rejects undefined `transport.options` and `client.description` before authorization or I/O; exact schema/runtime qualification remains open. | S3-01 |
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
fork, OpenAPI, HTTP raw and null-event behavior. Ordered-fork and explicit
null-event checks pass without expected-failure markers: root and principal
engine environments report nine passed and two expected assertion failures;
optional Agent Framework reports ten passed and two expected failures. The
remaining failures concern OpenAPI routing and HTTP raw binary data. Prior
unmarked runs confirmed the relevant output/request
assertions. Their strict, AssertionError-only xfails keep unrelated
errors failing CI and require marker removal when a repair makes the desired
behavior pass. Remaining expected failures are open defects. The passing fork
probes prove their actual subset, without closing complete inventory criteria.

The backlog and tracker retain earlier verified milestone scopes. The audit
adds missing repair ownership, including S2-01 for state collisions that were
previously mapped only to completed expression/data milestones. Conditional
backlog references do not activate optional Agent Framework hardening, external
brokers, push notifications or delegated identity work.
