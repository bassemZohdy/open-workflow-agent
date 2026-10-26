# S4-02 native stream observations

Status: the private foundation is merged through PR #84 (`73778d6`). An opt-in
public values/updates candidate is undergoing qualification. This record owns the stream work;
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

## Public original-owner observation candidate — 2026-10-26

The candidate adds the [configured source profile](native-streams.md), [bootstrap example](../examples/native-stream.yaml)
and [OWS workflow](../examples/native-stream-workflow.yaml). Selecting
`observability.native_streams` requires authenticated API ingress and original
execution authorization. Exact required `langgraph.streams@1.0.0` declarations
enable collection on the native adapter. Optional/absent declarations collect
no task data. Unsupported required profiles reject before `/v1/invoke` creates
an invocation. Default serialization and admission remain unchanged.

The HTTP endpoint verifies original ownership, current `invocations.stream`
permission and retained original authority before looking up observations.
It never owns execution, writes common recovery evidence or dispatches tasks.
Mode, frame, history, queue, response and aggregate registry budgets are bounded.
Global response slots survive log eviction until the actual response closes;
sender deadlines, cancellation and disconnect cleanup release them. Partial
projection/retention never silently advances a requested cursor. Waiting
segments finish their responses; a same-worker resume can extend the retained
incarnation. The actual abrupt-exit witnesses require unavailable old cursors,
recoverable native checkpoints and no repeated model call after resume.

The restart witness exposed a real Windows fenced-write issue. A selector main
loop passes admission, but the independent write thread previously used the
process-wide default Proactor policy, which psycopg rejects. The common write
helper now explicitly creates a selector loop using `asyncio.Runner` without
changing the process-wide policy. The worker retains a selector main loop and
the default global Windows policy, so both ordinary and thread-created loops
are covered. Cancellation still drains the native transaction before release.

Focused API/projection development checks pass on SQLite/PostgreSQL, including
original-principal isolation, explicit permission, changed-policy denial,
bounded replay/filtering, waiting/resume, network cancellation, subscriber
pressure, abrupt process exit and configuration-only startup. Strict typing,
Ruff and the earlier core buffer/registry/transport checks pass. Final frozen
native, common ownership/recovery and ADK compatibility checks are pending.

Failed attempts are retained under `tmp/merge-20261025/stream-api-*.xml`:
the test policy initially omitted `model.complete`; a four-event retention
test correctly reported a resume replay gap before a separate retained-interval
case selected a sufficient budget; the fresh worker exposed the Proactor error;
and the example initially used an unsupported prefix resource pattern. Those
fixtures/example are corrected without weakening authorization or replay rules.

## Remaining implementation and verification

The qualified private source above advertises no public stream capability.
The candidate opts in only to the named values/updates source profile.

Finish frozen qualification before claiming the public candidate verified.
Then continue other stream modes and the remaining full T50 criteria with
explicit access/redaction contracts. The portable lifecycle and A2A streams
retain their separate behavior; source qualification does not publish an image.

Native state updates, workflow replay and time travel retain their independent
ownership, fingerprint, receipt and checkpoint gates. This foundation neither
opens them nor completes any research inventory row.
