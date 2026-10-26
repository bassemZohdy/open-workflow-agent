# S4-02 native stream observations

Status: the private foundation is merged through PR #84 (`73778d6`). The opt-in
public values/updates profile is qualified at `a597134` below. This record owns the stream work;
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
native, common ownership/recovery and ADK compatibility checks pass at the
replacement source below.

Failed attempts are retained under `tmp/merge-20261025/stream-api-*.xml`:
the test policy initially omitted `model.complete`; a four-event retention
test correctly reported a resume replay gap before a separate retained-interval
case selected a sufficient budget; the fresh worker exposed the Proactor error;
and the example initially used an unsupported prefix resource pattern. Those
fixtures/example are corrected without weakening authorization or replay rules.

### Frozen public qualification — `e766fab`

Runtime and tests are frozen at
`e766fab59f71b32a472a5ac44d00054163188b2e`. The combined stream, ownership,
journal, native checkpoint and write-recovery selections ran on
Windows and read-only Docker/Linux with separate fresh ordinary PostgreSQL and
pgvector databases. Linux reuses the private profile's locked dependency image;
the vector service remains an optional test component. No package is installed
at container startup. Manifests, logs and XML are retained under
`tmp/merge-20261025/e766fab/`.

| Selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Combined stream/ownership/native-write/checkpoint regression | Intentionally stopped after the confirmed Linux failure; partial log retained | 317 passed, one startup-order failure, zero skips |
| Separate actual ADK authority/fencing/native-stream rejection | 18 passed, zero skips | 18 passed, zero skips |
| SDK-free core/API/configuration/recovery/tracking | 242 passed; 46 PostgreSQL cells initially skipped | Not selected |

The first Windows ADK helper selected a nonexistent `sqlite` extra and ran no
tests; its terminal output is retained as `adk-contract-windows-attempt1.log`.
The corrected helper selects ADK's own `native`/`postgres` extras and a fresh
database. It does not install LangGraph dependencies into the ADK environment.

The combined Linux run revealed that the new stream configuration check preceded
the established unsupported-Windows-loop preflight. Its minimal startup probe
therefore failed with missing configuration instead of the intended early
`unsupported_event_loop` error. Windows' slower duplicate selection was stopped
only after this specific failure was verified; it is not counted as success.
The guard order is restored, preserving rejection before service setup. All
seven actual adapter/startup cases pass the focused correction check. Final
qualification must use the replacement frozen commit and new databases.

### Replacement public qualification — `a597134`

The guard-only replacement runtime is frozen at
`a59713444e70bffd07d7681a4dc3feb8d2e6d8f2`. Its **318-case combined selection
passes on both platforms with zero skips**: 564.50 seconds on Windows and 218.96
seconds on frozen Linux. All **1,237 Linux source inputs remain unchanged**.
The separate 18-case actual ADK selection passes on both platforms with zero
skips. All use fresh databases.
The initial `e766fab` evidence above remains retained. Common runtime, ADK,
tests and example inputs are byte-identical between the two commits; the
SDK-free 242-pass/46-optional-skip result is retained for those unchanged inputs.
New source manifests and reports are under `tmp/merge-20261025/a597134/`.
The 46 omitted SDK-free PostgreSQL cells were selected directly from the original
XML and rerun against a fresh PostgreSQL database at `a597134`: **46 passed,
zero skips**, in 22.46 seconds. The baseline and supplemental selections cover
288 distinct core/API/configuration/recovery/tracking cells; they are separate
runs, not a single full-repository qualification.

| Replacement selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Stream/API, ownership/journal, native checkpoint and memory write/recovery | 318 passed, zero skips | 318 passed, zero skips |
| Actual ADK authority/fencing/required-stream rejection | 18 passed, zero skips | 18 passed, zero skips |
| Supplemental common PostgreSQL cells omitted from baseline | 46 passed, zero skips | Not selected |

## Remaining implementation and verification

The qualified private source above advertises no public stream capability.
The qualified public source opts in only to the named values/updates profile.

Continue other stream modes and the remaining full T50 criteria with
explicit access/redaction contracts. The portable lifecycle and A2A streams
retain their separate behavior; source qualification does not publish an image.

Native state updates, workflow replay and time travel retain their independent
ownership, fingerprint, receipt and checkpoint gates. This foundation neither
opens them nor completes any research inventory row.
