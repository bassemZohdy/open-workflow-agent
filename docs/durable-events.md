# Embedded durable events

Status: embedded runtime qualified at `143d38a`, with all four full suites and
seven configured gates passed. The [event capability report](../core/src/open_workflow_agent/event_capabilities.py)
now advertises this verified embedded profile, including its approval delegate.
Memory and arbitrary custom buses remain unqualified, even if a custom bus
declares its own durability. Lifecycle CloudEvents remain bounded snapshots.
The publication revision's final configured checks are pending in the
[finite acceptance record](s2-05-event-acceptance.md); S2-05 remains open until
they pass.

## Deployment

The default `events.backend: embedded` uses the common invocation datasource.
SQLite runs inside the existing container; PostgreSQL is an optional configured
alternative. No broker or additional service is needed for embedded SQLite.
Preserve the runtime database and the selected engine's native persistence when
restarting. Core event records contain common identities, envelopes and routing
decisions; native graphs, tasks and checkpoints remain engine-owned.

Bootstrap example:

```yaml
persistence:
  database: /data/runtime.sqlite3
events:
  backend: embedded
  max_event_bytes: 1048576
  max_retained_events: 10000
  max_retained_bytes: 67108864
  max_subscriptions: 1000
  max_outbox: 10000
  scan_batch: 128
  retention_seconds: 86400
  poll_interval_seconds: 0.05
```

These are deployment settings, not extra Open Workflow fields. The official
OWS 1.0.3 schema is unchanged. `events.backend: memory`, or an explicitly
injected process-local bus, preserves the public single-envelope bus interface.
Advanced `all`/`any`/correlation workflows require the embedded workflow event
interface; engine admission rejects unsupported deployment requirements before
dispatching earlier tasks. The memory backend does not provide event replay.
Its single-event iterator children use the existing listener task occurrence;
durable collections additionally bind each event ordinal to an immutable
selection receipt. This preserves native validation without requiring a
nonexistent durable receipt from a process-local bus.

## Workflow results and matching

Every `listen` task returns an ordered array, including a one-event listener.
`read: data` yields an array of payloads; explicit null yields `[null]`.
`read: envelope` yields normalized envelopes including supplied extension
attributes. `read: raw` yields their compact canonical JSON strings, not a
transport's original byte stream. Supported stored payloads are finite JSON.

| Strategy | Implemented completion and ordering |
| --- | --- |
| `one` | Complete on the first accepted matching event. |
| `all` | Retain partial selections until every filter is satisfied; output follows arrival order, not filter declaration order. One event can satisfy overlapping filters and is included once. Empty `all` completes with `[]`. |
| `any` | Without `until`, complete on the first accepted match. Empty `any` accepts incoming events in the listener's trusted scope. |
| `any` with expression `until` | Evaluate the condition against the candidate's selected read projection. A true candidate terminates consumption and is excluded from output. |
| `any` with strategy `until` | Collect terminating-strategy progress separately. Terminator matches take precedence and are excluded from output and iteration. A terminating empty `all` completes immediately. |

Event filters support literal JSON values, string regular expressions and
`${ ... }` predicates. Predicates evaluate against the filtered property's
value: a `with.data` predicate sees event data. String patterns are regular
expressions; use anchors when requiring an exact string. JSON matching and
correlation preserve null and distinguish booleans from numbers. Restricted jq
and bounded JSON do not imply hard CPU preemption of a predicate.
Static property restrictions are checked before dynamic predicates, regardless
of JSON key order. An unrelated event with a different payload shape cannot
fault the listener through a predicate it never needs to satisfy. Predicate or
correlation errors on eligible events still use the common expression error.

For example, a one-event workflow reads its first array element explicitly:

```yaml
document:
  dsl: '1.0.3'
  namespace: examples
  name: order-ready
  version: '1.0.0'
do:
  - ready:
      listen:
        to:
          one:
            with:
              type: '^example.order.ready$'
            correlate:
              orderId:
                from: '${ .data.orderId }'
                expect: '${ .orderId }'
        read: data
  - finish:
      set:
        orderId: '${ .[0].orderId }'
```

`correlate.<name>.from` extracts from the filtered event envelope. `expect` is
evaluated once against listener task input and workflow variables at registration;
a literal expectation is also supported. An omitted expectation binds the first
accepted extraction. Named bindings and partial filter/terminator progress are
persisted atomically with immutable routing receipts. Restart uses those saved
bindings instead of choosing a different first value.

## Sequential iteration

`foreach` is a task member beside `listen`. Its `item` and `at` defaults are
`item` and `index`; the index is zero-based. Optional `do` tasks execute for one
event before the next event is processed. `foreach.output.as` and its schema
apply to each item; `foreach.export` updates workflow context. The listener's
final array contains the item projections, and the enclosing task's normal
output/export pipeline follows.

Iteration reuses existing scope routing, lexical variables, task references,
retry/timeout controls and native task checkpoints. An `exit` leaves its nested
task scope; `end` uses the existing workflow-wide termination behavior. A stable
event ordinal binds nested external-action receipts, allowing recovery after a
known action outcome without another dispatch. Core persists selected events
and routing decisions, not a duplicate native iterator checkpoint.

## Acceptance, delivery and restart

API publication commits a normalized inbox event before acknowledging it.
Workflow `emit` commits its canonical envelope, outbox entry and immutable
common outcome receipt in one transaction. Generated envelope values are chosen
once for that logical publication and are reused during recovery. The workflow
acknowledgement proves durable local acceptance; it does not mean every listener
has processed the event.

The local dispatcher inserts/deduplicates the inbox entry and marks the outbox
entry delivered atomically. Active workflow and direct bus receivers drain
bounded outbox batches while polling. The current adapter does not launch an
independent background broker worker. An accepted outbox entry remains pending
until a receiver drains it, including after restart.

Registration commits before native waiting progress or public waiting status is
visible. Per-event and routing receipts commit before native processing links.
On restart, the same full task occurrence reuses its original selections and
correlation decisions. Saved envelopes, subscriptions, receipts and profile
bindings are checked before recovery changes the parent or dispatches work.
Known terminal child dependencies are checked recursively.

The embedded store serializes writers through a transaction-scoped arrival
counter. SQLite uses its writer transaction; PostgreSQL locks the common counter
row after any invocation ownership fence. Allocation and commit therefore
cannot let a later committed sequence make a listener skip an earlier delayed
commit. Ordering is committed local arrival order, not client-supplied `time`.
This global admission order is a throughput tradeoff; broader measured capacity
and performance comparison remain S4-06.

## Duplicate, early and late policy

These are explicit embedded delivery rules, not universal OWS or external
transport guarantees:

- Deduplicate by trusted delivery scope plus `source` and `id`. Equivalent
  resubmission returns the original envelope, including generated time. Changed
  content under the same identity fails closed with a sanitized validation error.
  Different sources or trusted scopes are distinct. Callers omitting an event id
  receive a new generated identity; that does not establish caller-level dedupe.
- A new invocation may consume retained events received before its listener
  registration. When a new listener is registered within an invocation, its
  lower bound excludes events already selected before that registration by any
  listener in the invocation. Sequential tasks and repeated loop occurrences
  therefore cannot consume one historical signal twice.
- Already registered listeners preserve their own lower bounds. Concurrent
  fork listeners can receive the same broadcast arrival. The invocation cursor
  never replaces full occurrence identity in immutable receipt keys.
- Completed or closed subscriptions never reopen from a late event. Common
  authorized cancellation closes pending subscriptions after terminal state
  commits; selection also checks persisted terminal state. Process shutdown or
  raw coroutine interruption leaves pending registrations available for recovery.
- Legacy nonterminal event invocations without the corrected private event
  profile fail recovery admission. Earlier scalar listen checkpoints are not
  silently reinterpreted as arrays. Compatible legacy/version migration remains
  S2-09.

There is no general exactly-once delivery or external-action claim. Duplicate
suppression and replay apply to retained local evidence; external uncertain
intents retain their existing explicit-reconciliation policy.

## Bounds and retention

| Setting | Enforced meaning |
| --- | --- |
| `max_event_bytes` | Maximum encoded normalized envelope, prepared strategy and dynamic correlation/progress values. |
| `max_retained_events` | Inbox arrivals plus reserved future inbox arrivals for accepted pending publications. |
| `max_retained_bytes` | Stored inbox/outbox envelope bytes plus required future inbox reservations; not a total database-file-size limit. |
| `max_subscriptions` | Retained subscription rows, including completed/closed rows. |
| `max_outbox` | Retained outbox rows, including delivered publication evidence. |
| `scan_batch` | Maximum arrivals scanned or outbox entries delivered in a bounded batch. |
| `retention_seconds` | Expiration eligibility for explicit bounded pruning of unreferenced arrivals. |
| `poll_interval_seconds` | Interval between polls without new committed routing progress. |

An accepted pending publication reserves inbox space before acknowledgement.
Other API ingestion cannot consume that reservation. Capacity exhaustion rejects
new work without silently dropping acknowledged events or overwriting immutable
outcomes.

`EventStore.prune` preserves selected event evidence, delivered outbox bindings
and unscanned arrivals pinned by a pending listener in the same scope. The
current runtime does not schedule this sweep automatically and does not expire
all evidence after 24 hours. Recoverable records and closed subscriptions remain
under the configured capacity limits. Duplicate suppression lasts while the
canonical evidence is retained; pruning an eligible unreferenced arrival removes
that inbox identity's dedupe history. General history/version disposal remains
separate from this S2-05 acceptance.

## Authorization and staged boundaries

API event scope comes from the deployment-authenticated principal established
by middleware; workflow scope comes from the common invocation owner. An
anonymous deployment uses the anonymous scope. Payload `principal`, `tenant`,
`user_id` and invocation fields do not establish authorization or override scope.
This is principal scoping, not qualification of a multi-tenant product.

Generic publication cannot forge protected approval decisions. Approval request
projection and event acceptance share the transaction; authorized approval
decisions and decision ingestion also commit atomically in the trusted original
request scope. A listener for a protected decision is checked against the
authoritative approval record, even when using broad regular-expression filters.

Durable timers and schedule-trigger activation remain S2-06. Optional external
brokers, AsyncAPI bindings and delivery/security contracts remain S4-10.
Multi-tenancy remains S4-11. An embedded inbox is not durable lifecycle SSE
history, full CTK/proposed-test qualification or JavaScript implementation.
The schema remains unchanged and native execution state remains private.

## Error URI provenance

Built-in semantic error types follow the immutable OWS 1.0.3 release reference,
commit `9b5b1da29e9d4fff2358580241e11aab22704a16`, whose namespace is
`https://serverlessworkflow.io/spec/1.0.0/errors/`. Task/item output-schema
validation uses its `validation` type. User-declared error types retain their
declared value. See the [release standard-error table](https://github.com/open-workflow-specification/specification/blob/9b5b1da29e9d4fff2358580241e11aab22704a16/dsl-reference.md#standard-error-types).

Schema admission separately pins the unchanged schema revision
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a`; its later reference text uses the
`open-workflow-specification.org` namespace. The release URI policy does not
rewrite schema provenance or adopt floating upstream-main semantics. Both
immutable reference versions establish ordered listen arrays, FIFO iteration,
terminator exclusion and correlation. See [schema provenance](../resources/open-workflow/1.0.3/SCHEMA_SOURCE.md)
and the [schema-revision reference](https://github.com/open-workflow-specification/specification/blob/2dd2c84170d5f3e05d58e913e9ca298dcf8d543a/dsl-reference.md#standard-error-types).
