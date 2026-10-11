# Current implementation audit

S1-08 compares every tracked feature with active admission/execution code and
the scope of actual test assertions. The machine-readable
[implementation audit](../resources/compatibility/implementation-audit.json)
contains all **658 feature IDs** and 77 evidence groups. Each entry preserves
its original construct, proposed tests, independent Python/JavaScript outcome,
remaining scope and milestone owners. Source symbols and test functions are
linked explicitly; no predecessor reference file serves as runtime proof.

The reviewed implementation is
`51f7259`, which retains the verified ordered/competitive fork corrections and
adds original-input binding, atomic terminal records and adapter finalization
reconciliation. Its full local suites passed; exact new GitHub/database gates
are pending. Recovery and lifecycle remain partial, and S2-03 remains open for
general effects, recovery ownership and native checkpoint consistency.

Earlier competitive runtime, fixture/audit and all three common PostgreSQL
gates passed at `2fbe548`, `51c204e` and `81d27b3`. The JSON null wake-up fix also
passed every exact gate at `f2943c6`. See [competitive acceptance](../PROJECT.md#s2-02-competitive-fork-decisions--2026-10-11-verified)
and [recovery progress](../PROJECT.md#s2-03-checkpoint-recovery--2026-10-11-in-progress).
Hashes of reviewed Git file contents preserve the evidence baseline. The
manifest's `previous_reviews` retains the earlier S1-08 acceptance and points
to its immutable artifact. Historical green gates do not qualify changed code.

## Outcomes

| Python audit outcome | Features | Meaning |
| --- | ---: | --- |
| Qualified subset | 181 | Specific bounded behavior has tests; full inventory criteria remain unverified |
| Partial | 149 | Some requested behavior exists, with explicit missing scope |
| Behavior mismatch | 32 | Accepted behavior differs from the tracked construct |
| Rejected | 197 | Admission/client guard rejects the request; this is failure evidence, not implemented support |
| Inert | 27 | Field is accepted/preserved without its requested effect |
| Internal only | 9 | Native primitive is used privately, without a general public capability |
| Unimplemented | 63 | No public implementation found in the reviewed paths |

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
| Native checkpoint recovery | Private scope savers recover completed calls. Original raw input and empty/false resume values are separate; atomic terminal value/null/error records survive interruption and late finalization. General pending effects, ownership, checkpoint consistency and legacy migration remain unproved; new configured database acceptance is pending | S2-03 in progress; migration S2-09 |
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
