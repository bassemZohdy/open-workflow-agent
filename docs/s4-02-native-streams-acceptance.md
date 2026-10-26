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

## Private foundation verification

Runtime source is frozen at `baebb73487e3b3c5dfc7f6ed46f83ddbc40b1898`.
Before freezing, all seven actual SDK projection cases passed against SQLite
and PostgreSQL: both observation modes preserve one model dispatch and final
workflow output, closed/oversized observation cannot fault execution, private
native fields are absent, and another invocation's observer is rejected before
execution. The final core transport suite passes 29 cases, including cancelled
reads and explicit projection failure. Four tracking guards, strict mypy over
159 source files, Ruff, formatting and Markdown links pass.

The frozen Windows and Docker/Linux selections each pass **106 cases with zero
skips**, in 215.26 and 70.64 seconds respectively. They include 29 new core
transport cases, seven actual SDK projection cases, 36 existing native
task/adapter/resume cases, eight shared fencing cases and 26 authority cases.
The 34 opposite-engine cases are deliberately deselected. Separate fresh
PostgreSQL databases keep the platform runs independent. Linux reuses the immutable dependency image
`sha256:591b1bc4959940d8c6d7fa4f5c5b2471ddf2d68570e04415cbc420981f5e9f42`
only after checking matching root/engine locks, with read-only root/source,
non-root execution, dropped capabilities and bounded resources. Evidence is
retained under `tmp/merge-20261025/baebb73/`. All **1,226 frozen source inputs**
remain unchanged. This qualifies the private component only; no public native
streaming, full T50, full-repository or release claim follows from these checks.

## Remaining implementation and verification

Public capability and `EngineCapabilities.streaming` remain false throughout
this qualified private foundation.

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
