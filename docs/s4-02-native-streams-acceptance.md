# S4-02 native stream observations

## Native subgraph namespace boundary — 2026-11-02

The current runtime's independently configured child scope preserves its
separate thread and empty checkpoint namespace, but a direct SDK
`subgraphs=True` probe does not propagate that child's events to the parent
stream. Conversely, inheriting the SDK parent context propagates nested events
but stores the child checkpoint under the SDK's generated non-empty namespace;
the current child checkpoint lookup no longer finds it. Simply enabling
`subgraphs=True` or inheriting the context therefore cannot qualify the
existing recovery contract.

The test-only prototype in
[`test_native_subgraph_sdk_boundary.py`](../tests/langgraph/test_native_subgraph_sdk_boundary.py)
translates the logical SDK namespace to the existing physical empty namespace
for a simple child graph. Eight cases passed with zero skips on SQLite and
PostgreSQL, including a fresh saver reopening the child checkpoint, a
pending-write round trip and one SDK interrupt/resume without repeating a
deterministic callback. This is feasibility evidence, not runtime code or a
supported stream profile. A later focused check also passed a basic `alist`
history and `before` pagination round trip on both stores. Its first attempt
incorrectly listed with a selected checkpoint ID and returned one item; the
corrected check lists by thread and namespace before applying pagination.
This does not qualify every `alist` filter or limit, OWA's native
interrupt/recovery contract, real process failure or effect receipts,
concurrent/repeated scopes, branch routing, lineage verification or safe public
namespace projection. A runtime implementation must preserve those identities
and gates before advertising the subgraph capability; S4-02 and T50 remain open.

## Declared custom diagnostic source profile — 2026-11-02

The separate required `langgraph.streams.custom@1.0.0` profile uses the native
SDK custom writer for workflow-declared root-task diagnostics. It requires the
base stream profile and one to 64 immutable bindings. Each binding selects a
bounded literal JSON value or the completed task's output. Literal strings are
not evaluated as jq. Events are bounded, original-owner observations; they are
not published to the Open Workflow business event bus and do not prove a
checkpoint committed. A writer failure or oversized frame ends observation
without changing the workflow outcome. The [operator guide](native-streams.md)
documents the configuration and its limits.

Local SQLite/PostgreSQL checks cover native writer use, optional declarations,
admission limits, original-owner access, failure isolation, separate business
event delivery, resume, replay, process exit and malformed native events. The
complete stream/API, projection, buffer, registry, transport and custom-config
selection passed **189 cases with zero skips** against a fresh PostgreSQL
database; its JUnit report passed the hosted job's witness gates. Common
workflow/admission and CI selection checks passed 82 cases, the ADK adapter
rejected the required extension, and strict type checking passed on 194 source
files. An earlier PostgreSQL attempt on a database used by a prior candidate
returned `runtime_profile_changed`; a subsequent fresh-database selection
passed. Hosted qualification, remaining native stream modes, T50 and full
S4-02 remain open.
Frozen source `f0f29a5` was submitted on
`codex/s4-02-custom-stream-qualification-f0f29a5` in
[workflow run 41423898222](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/41423898222).
Its native-stream job passed **189 cases with zero skips** in 90.88 seconds on
hosted Linux. Its separate branch job is running and the full workflow is not
yet complete; broader T50 and S4-02 qualification remain open.

## Timestamped debug source profile — 2026-11-02

The separate required `langgraph.streams.debug@1.0.0` declaration observes a
bounded projection of the pinned SDK's native debug stream. It requires the
base stream profile. Public events contain a timestamp and either a public
task reference with lifecycle status or a root-checkpoint occurrence. Native
step numbers, IDs, task inputs and outputs, exceptions, checkpoint contents and
nested checkpoint events are excluded. Malformed SDK events terminate
observation without taking over execution. No checkpoint reference is emitted
by this profile, and it does not grant state-read access.

Local SQLite/PostgreSQL checks cover required and optional declarations, owner
isolation, nested tasks, failures, interruptions, resume, native Send and replay
targets. Eight actual-process exit/restart cases across the base, task,
checkpoint and debug profiles and four malformed-debug projection cases pass;
the ADK adapter rejects the required capability. A parallel test attempt sharing
one PostgreSQL database produced `runtime_profile_changed` and a separate
state-unavailable response. A subsequent isolated selection using a fresh
database passed 10 cases; the latter response did not expose enough detail to
prove its cause. The isolated complete stream/API, projection, buffer,
registry and transport selection then passed **151 cases with zero skips** on
SQLite and PostgreSQL. Its JUnit report also passed the CI witness gates;
strict type checking passed on 194 source files. Hosted qualification and the
remaining stream modes, T50 and full S4-02 remain open.
Frozen source `2577879` was submitted on
`codex/s4-02-debug-stream-qualification-2577879` in
[workflow run 41420479657](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/41420479657).
Its native-stream job passed **151 cases with zero skips** in 92.63 seconds on
hosted Linux. The overall workflow is still running, and later custom-stream
changes and full S4-02 remain separately gated.

## Root checkpoint source profile — 2026-11-02

The separately required `langgraph.streams.checkpoints@1.0.0` profile adds
opaque root-checkpoint references to the native stream. It is advertised only
when both the native stream and native state reader are installed, and it
requires both corresponding workflow declarations. SDK checkpoint IDs,
threads, nested scopes and native state are not projected. Observations are
not proof of a committed or retained checkpoint; state reads independently
enforce their existing ownership, authorization and evidence checks.

Local SQLite/PostgreSQL checks cover optional versus required declarations,
missing dependencies, reference matching against root history, nested-scope
filtering, original-owner isolation, malformed native identity, restart and
replay targets. A separate permission test confirms that a stream reference
does not grant `state.inspect`. Six process-exit cases cover the base, task and
checkpoint profiles across both stores, and the actual ADK adapter rejects the
required capability. The strict repository type check passes. This source
profile has not been hosted-qualified or published; other stream modes, T50 and
full S4-02 remain open.

The combined stream, projection, buffer, registry and transport selection
passed locally with **124 cases and zero skips** on SQLite and PostgreSQL; the
separately added no-`state.inspect` permission check passed on both stores.
Existing owner/state inspection regressions passed six cases. These runs do
not qualify a hosted image or the broader T50 criteria.
Frozen source `13aa837` was submitted on
`codex/s4-02-checkpoint-stream-qualification-13aa837` in
[workflow run 41418377253](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/41418377253).
Its native-stream job passed **126 cases with zero skips** in 45.12 seconds on
hosted Linux. The overall workflow is still running; later debug/custom stream
changes and full S4-02 remain separately gated.

## Task lifecycle source profile — 2026-11-02

The separate required `langgraph.streams.tasks@1.0.0` declaration extends the
configured base stream with bounded native task lifecycle observations. It
requires `langgraph.streams@1.0.0`; optional or absent task declarations emit
no task events. Projection exposes only a public scope/task reference and a
started/completed/faulted/interrupted status. It does not expose SDK task IDs,
input, results, exceptions, triggers or interrupt payloads. Invalid native
shapes fail the observation closed without taking over workflow execution.
See the [operator guide](native-streams.md) for authorization and retention.

Local SQLite/PostgreSQL checks cover required versus optional declarations,
original-owner access, normal/faulted/interrupted and nested tasks, resumed
cursor continuity without repeating the model effect, and rejection without the
required base profile. The combined existing stream/projection/registry/transport
regression passed 94 cases on the two stores; a separate three-case resumed-task
and malformed-projection selection passed on both stores. Four actual-process
exit/restart cases passed across the two stores with and without the task profile:
old cursors become unavailable, native resume completes, and the prior model
effect is not repeated. Actual ADK rejects
the new required capability before native mutation. The repository-wide strict
type check passes after three existing lineage typing issues were corrected.
These are local development results; hosted qualification, other stream modes,
T50 and full S4-02 remain open.

A follow-on SQLite/PostgreSQL integration check replays a retained checkpoint
after an effect and verifies that the new invocation emits task statuses only
for its continued task. The source effect is not repeated, and the new target
remains independently observable by its owner.

Separate local SQLite/PostgreSQL coverage combines the task profile with a
non-competing native Send fan-out. Both branches emit their own started and
completed public task events, and observation remains available through the
join. This follow-on case is newer than the frozen hosted source below.

The dedicated PostgreSQL CI job now selects the complete stream/API,
projection, buffer, registry and transport suite on both stores, requires at
least 90 cases, zero skips and the named task-profile lifecycle witnesses, and
retains its JUnit report. Its exact selection passed locally with **100 cases,
zero skips**. Hosted results are recorded separately when the frozen run ends.
The frozen source `568f52d` is being qualified on
`codex/s4-02-stream-qualification-568f52d` in
[workflow run 41416534381](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/41416534381);
the dedicated Linux stream job passed **100 cases with zero skips** in 57.94
seconds. The overall workflow is still running; this does not qualify newer
checkpoint, debug or custom profiles or complete S4-02.
Its separate native state-branch job hit the earlier 35-minute timeout at 96%
(exit 124); its unfinished cases are not counted as passes. Source `f0f29a5`
contains the subsequently increased bounded allowance and is queued separately.

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
