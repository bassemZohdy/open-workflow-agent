# S4-02 native stream observations

Status: private foundation in development after PR #83 (`872d424`). Public
native streaming remains disabled. This record owns the new stream work;
[memory qualification](s4-02-native-memory-acceptance.md) and [channels/Send](s4-02-native-channels-acceptance.md)
retain their separate evidence. S4-02 and T50 remain open.

## Contract and ownership boundary

Consumers are applications observing their original authenticated invocation.
The adapter produces observations; a future common HTTP endpoint must select a
log only after the existing original-owner check and a separate stream-read
authorization action. A correlation user/session label or a replay cursor never
grants access. Core owns bounded transport; only the adapter understands native
SDK values and updates. Engine checkpoints and execution journals stay separate.

The private first profile projects only current task data and public OWS scope
and task references from the pinned SDK's `values` and `updates` modes. It never
serializes the native snapshot envelope, graph/node/thread/checkpoint IDs,
receipts, routing counters, variables, context or internal plan. Data values
can still be sensitive: this is a data-bearing observation boundary requiring
original ownership and explicit authorization, not the existing sanitized
lifecycle diagnostic stream. Messages, debug, checkpoints, tasks, custom modes
and JavaScript tool streams remain unsupported.

Observations are asynchronous, process-local and best effort. They reflect SDK
emission order and do not prove a checkpoint commit or a business effect. One
native `astream` drives execution with synchronous durability; it replaces the
single private `ainvoke` only when an internal observer is supplied. No second
graph invocation manufactures observations. Ordinary execution keeps its
existing driver. A missing/closed/slow observer cannot cancel or stall business
execution; an unprojectable or oversized event ends observation explicitly.

Private JSON envelopes use `owa/native-observation/v1`, an invocation ID, mode,
public scope/task reference and projected data. Immutable encoded bytes detach
them from later runtime mutations. Defaults retain at most 256 events/1 MiB,
at most 64 KiB per frame, 32 consumers and 64 queued events per consumer. Limits
are validated before constructing a log. Eviction respects both count and bytes;
slow consumers terminate explicitly without blocking producers or other readers.

Opaque cursors contain a random per-log incarnation and ordinal. Only a cursor
whose entire following interval is retained may replay. An expired, malformed,
future, other-invocation or prior-process cursor fails explicitly. Registering a
live subscriber and taking its backlog are one event-loop operation. This is
observation replay, not workflow replay, durable retention or a resume command.

## Remaining implementation and verification

The private buffer and real SDK projection must pass their focused tests plus
the relevant native/authority contracts before qualification. Public capability
and `EngineCapabilities.streaming` remain false throughout this foundation.

Next, implement the opt-in deployment policy and bounded per-runtime registry,
original-owner HTTP observation endpoint, explicit capability/admission gate,
stream lifetime/event/byte limits, replay errors and disconnect cleanup. Retain
the current portable lifecycle and A2A stream contracts. A management read must
never dispatch or resume a workflow. Test two simultaneous original principals,
denied actions, response data boundaries, independent observers, waiting/resume,
actual abrupt restart, SQLite and optional PostgreSQL before advertising it.

Native state updates, workflow replay and time travel retain their independent
ownership, fingerprint, receipt and checkpoint gates. This foundation neither
opens them nor completes any research inventory row.
