# Recorded implementation acceptance history

Historical evidence moved verbatim from `PROJECT.md` during the 2026-10-13 documentation cleanup; only local link destinations were relocated. Dates, revisions, failures, limitations and acceptance claims describe their recorded sources. See [current status](../../PROJECT.md) for unfinished work.

## Architecture

### Dependency audit correction — 2026-10-11 (verified)

The historical dependency audit parser read the top-level report instead of
`dependencies[].vulns[]`. Earlier successful Security jobs remain historical
run results and do not prove absence of fixable dependency advisories. Their
runtime, checkpoint and container test results remain independently recorded.
The Security run at `f63b801` failed without a report after a registry timeout;
its other jobs also used the defective parser.

Actual downloaded ADK and LangGraph reports reproduce the false pass: four and
three fixable advisories respectively. The corrected parser rejects both. A
fresh root audit also finds three pypdf advisories. Patched urllib3 2.8.0,
multidict 6.9.1 and pypdf 6.19.0 are selected in the four independent locks;
root and standalone-core pypdf requirements agree. The gate validates nested
advisories, scanner status and report completeness. It rejects missing,
malformed, skipped or contradictory reports. Unfixable advisories remain
visible warnings under the existing policy. Registry failures receive one
bounded retry when no report exists, never advisory suppression. Each runtime
environment now audits all its optional model, knowledge and persistence
dependencies. All six fresh local audits report no known advisories across
74 root, 112 ADK, 99 LangGraph, 77 optional Agent Framework, 13 Docker-controller
and 16 Kubernetes-controller dependencies. Twenty-six deterministic parser
tests pass. Executing the actual workflow script with a fake scanner also
proves clean-report success, fixable-advisory failure, visible unfixable
warnings, contradictory/failed-scan rejection and exactly two attempts before
missing-report failure.

Frozen-source regression with the patched dependencies passes 1,403 root
tests, 163 skips, three known expected gaps and 91.79% coverage. Independent
native/shared/CTK suites pass ADK 695 (122 skips), LangGraph 708 (129 skips) and
optional Agent Framework 945 (185 skips), each retaining the same three gaps.
Formatting, lint, strict typing, all four locks, all five package builds and
documentation checks pass. All exact `fc53574` gates passed: [CI
38161547695](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161547695),
[push CI 38161544737](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161544737),
[PostgreSQL 38161547613](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161547613),
[external sandbox 38161547672](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161547672),
[optional Agent Framework 38161547666](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161547666),
[Security 38161547761](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161547761),
[push Security 38161544729](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161544729)
and [Docs 38161547668](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38161547668).
Linux root passed 1,407 tests with 159 skips, three expected gaps and 91.82%
coverage. All six downloaded Linux reports contain zero known advisories across
71 root, 109 ADK, 96 LangGraph, 74 optional Agent Framework and 13/16 controller
dependencies; platform dependency counts differ from Windows. The configured
PostgreSQL matrix and both runtime-image restart jobs passed.
The patched versions are confirmed by the
[urllib3 release](https://github.com/urllib3/urllib3/releases/tag/2.8.0),
[multidict advisory](https://github.com/aio-libs/multidict/security/advisories/GHSA-54p9-h82j-f925)
and [pypdf release](https://github.com/py-pdf/pypdf/releases/tag/6.19.0).

### S2-04 retry, timeout and exception work — 2026-10-12 (verified)

Final qualification at `6f8e95d` verifies all thirteen scoped requirements
against source `29eeb3b`, with production/schema bytes unchanged from the four
full-local suites at `a0ee9c0`. All seven exact configured runs succeed. Actual
PostgreSQL cases pass per principal: five provider receipts, eleven timing,
eight deadline, five retry-schedule and three real process-stop cases, plus
common atomic policy and intent rollback. Both Windows stages pass in push
and PR CI; each run passes both main runtime restart images and both native
benchmarks. Both PostgreSQL restart images pass. Six nonempty dependency
reports contain zero nested advisories. Exact IDs and terminal suite counts
are retained in the implementation audit. The earlier three/four and five/two
success/failure artifacts remain historical evidence, not qualified releases.
S2-04 is checked; 13 of 37 tasks are verified. Full proposed
criteria, JavaScript and later dependency/capability gates remain open.

### S2-06 durable timer and schedule work — 2026-10-13 (verified)

Common durable timer decisions, adapter-owned nonresident suspension, fenced
nominal schedule slots, atomic invocation/input binding and event-created
invocations are implemented. Actual ADK/LangGraph timer, deadline, fork,
receipt-corruption and forced process-stop checks pass. The Open Workflow
1.0.3 schema remains unchanged; calendars include UTC/Dubai, DST and the
specified day-of-month/day-of-week OR behavior.

Frozen local suites pass root 2,073/1,007 skipped with 91.05% coverage,
LangGraph 1,256/750 and optional Agent Framework 1,543/1,077; each retains two
tracked integration gaps. ADK passes 1,212/777 with those same gaps and one
isolated benchmark failure: its 50 ms wait legitimately elapsed during native
receipt persistence. A deterministic probe-clock correction preserves that
duration and performance measurement. All 274 frozen inputs remained
unchanged during each full run. Before the first push, the only changes after those full runs are the
benchmark harness and a direct PostgreSQL two-connection timer-claim fixture.
The first pushed source `4cdf552` fails actual PostgreSQL on a nullable query
parameter and single-precision schedule timestamps; its root CI also exposes
two legacy fork cancellation assumptions. Corrections use a typed optional
filter and double-precision clocks, preserving strict evidence checks and
existing stored values, plus acknowledged-suspension fork and shared-session fixtures. Corrected
shared scheduling/trigger focus passes 77 cases; actual ADK/LangGraph fork
files pass 18 each and recovery admission passes seven each. Optional Agent
Framework fork/recovery focus passes 27/three. Both rebuilt packages contain
all 64 matching core Python files. At `3d2faf0`, actual database wall-clock,
timer-claim and fractional-schedule controls pass; one legacy DDL test needs
consistent SQL precision for its before/after comparison. At `3dab25a`, the common-store step passes and native LangGraph database
recovery passes 342 cases with one schedule-fixture contamination failure.
A bounded fixture sweep preserves all receipts/inputs/corruption controls,
isolates controlled schedule delivery, and validates six actual deadline/retry
process stops plus two historical checkpoint stops across both SDKs.
At `ec94b17`, PR CI, Docs, Security, External Sandbox and optional Agent
Framework pass; all four native benchmark artifacts and both main images are
inspected. PostgreSQL passes the core and all LangGraph recovery cases, then
fails one ADK event-worker counter contaminated by unrelated timer delivery.
Worker-only isolation preserves exact call assertions; all eight event/timing
process cases pass on each actual SDK. Fresh PostgreSQL/images and all configured
acceptance remain required; the original push Windows cancellation remains
historical. At `5e66f99`, both main CI runs succeed, including Windows and main
images. The remaining PostgreSQL failure is three explicit timeout fixtures
followed by the unchanged ADK step limit. Isolated retry/event module focuses
pass 92 cases per actual SDK, and automatic Wait CTK still passes. Fresh exact
PostgreSQL acceptance remains required before S2-06 closure.

Final acceptance at `3f568e591309e1a6321ed44452d6e710cd6d326b` passes all six
configured workflows: CI, PostgreSQL, optional Agent Framework, External
Sandbox, Security and Docs. Root passes 2,132 cases at 90.90% coverage; native
ADK passes 1,127 and LangGraph 1,170. Each CTK subset passes 96 scenarios with
all 26 committed scenario hashes verified. Four Windows selections pass
466/247/211/273 cases. Actual PostgreSQL passes 290 common-store, 348 native
LangGraph, 314 ADK and 152 optional Framework cases, including all 42 finite
timer/schedule cases per principal and both database-backed images. Both main
runtime images also pass the exact scheduling capability assertions. Six
nonempty dependency reports contain zero advisories; both native benchmarks
retain exactly one side effect after resume. Two existing protocol integration
gaps remain expected failures under S3-03; unavailable-profile skips do not
count as native/database acceptance.

S2-06 is checked for its finite Python profile. The
[acceptance record](../s2-06-timer-schedule-acceptance.md) preserves the earlier
failures, timeout and cancelled superseded run. Default deployment remains one
container; broader scale-out, child lifecycle S2-07, external transports and
full proposed/JavaScript qualification retain their separate gates. Fifteen of
37 active tasks are verified; no task is deferred.

### S2-05 embedded event work — 2026-10-12 (verified)

The working implementation adds an embedded durable inbox/outbox, immutable
subscription and consumption decisions, ordered one/all/any/until results,
correlation and sequential listener iterators. Focused checks pass on the
principal native engines and optional Agent Framework; actual process-stop
recovery passes on both principal engines. A prior-source probe fails eight
desired-behavior assertions, demonstrating the original gap.

At `143d38a`, all four full suites and all seven configured workflows pass.
Core passes 1,910/888/two tracked gaps at 91.32% coverage; ADK 1,109/688/two,
LangGraph 1,151/661/two and optional Agent Framework 1,408/946/two. Actual
PostgreSQL executes all 41 durable event and five process cases per principal;
Windows, both image/restart pairs, benchmarks, distributions and six nested
zero-vulnerability reports are verified. The capability publication update
reports durability only for the qualified embedded bus and its approval
delegate; root/native focused checks pass. Final publication `58de90e` passes
all seven exact configured gates with actual artifact review. All E01-E14
requirements are verified. S2-05 is checked; 14 of 37 active tasks are verified,
and S2-06 is next. There are no deferred tasks.

Seven desired error-context cases fail on actual native LangGraph at `d8f3f7a`:
built-in status filters lose the common status code, while catch/retry predicates
cannot consistently read the configured default/custom error variable. Source
`23b9d35` binds that variable before filter and predicate evaluation, preserves
declared OWS fields, and provides built-in common status without changing public
error serialization. Common provider errors retain their code/message/status at
task and receipt boundaries; fresh native recovery reconstructs Model, Agent,
Knowledge and Memory errors. Unexpected provider exceptions remain sanitized.

Core workflow/journal focus passes 54/1. Native/portable/recovery suites pass
95/29 in each independent principal SDK environment. The final error fixture
passes 19/15 per environment, including five actual native provider-receipt
replays; the missing other SDK and unconfigured PostgreSQL cases are skips.
Nine real collection guards select the five new PostgreSQL recovery cases per
principal adapter. Strict package/fixture typing, lint and formatting pass.
Changed-source PostgreSQL and full/configured regression remain pending.

The task stays unchecked: jitter, retry duration/attempt limits, stable deadlines
and retry state, timeout cleanup/attribution, complete scoped exception behavior
and full task acceptance remain required. Later proposed/JavaScript criteria
retain their Stage 4 owners. The prior S2-03 acceptance remains source-bound.

Error-context artifact `b6aef8c` subsequently passes all seven configured runs,
including five actual PostgreSQL provider-receipt cases per principal adapter,
both Windows suites, both main/PostgreSQL images and native benchmarks. Core
passes 1,684/637/three tracked gaps at 91.68% coverage; native ADK 841/509 and
LangGraph 883/482 plus 96 CTK each; optional Agent Framework 1,229/723/three gaps.
Windows passes 460/12 core and 477/299 native. Six nested dependency reports
contain zero known advisories. This qualifies the error block, not full S2-04.

Source `f2b5c02` implements typed retry timing: additional-attempt limits,
declarations without a count, constant/linear/exponential delay, bounded additive
jitter, per-retry duration and aggregate runtime duration including waits. The
aggregate clock starts when the first retry is selected. Foreign TimeoutError
is distinguished from runtime deadlines, and synchronous elapsed work is checked.
Deadline cancellation drains the active action and retains uncertain intent;
cancellation wrappers cannot discard that uncertainty and enter catch retries.
The same new assertions fail ten actual native LangGraph cases on archived
`b6aef8c` source (one case passes). Current core passes 64/1; native timing/error/
receipt focus passes 85/39 per SDK and lifecycle/fork regression 93/26 per SDK.
The final timing fixture passes 22/22 per environment; those 22 skips require
configured PostgreSQL. Eleven actual collection guards select eleven timing
cases per principal adapter. Strict 67-source/fixture typing and full scoped
lint/formatting pass. Changed timing-source full/configured acceptance is pending.

Persisted original workflow/task deadlines and retry clock/count/schedule,
stable jitter decisions across restart, complete scoped exception behavior and
full S2-04 acceptance remain required. The task stays unchecked. The eight
previously inert jitter/duration inventory fields are now partial, rather than
complete, and full proposed/JavaScript flags remain unchanged.

Runtime-timing artifact `cbb06b2` passes all seven configured runs. Terminal
logs confirm eleven PostgreSQL timing cases per principal adapter: 239 LangGraph,
205 ADK and 147 optional bridge passes (seven optional skips), plus 119 store checks
and both images. Core passes 1,718/659/three tracked gaps at 91.80%; native ADK
863/531 and LangGraph 905/504 plus 96 CTK each. Optional Agent Framework passes
1,262/756/three gaps. Windows passes 460/12 core and 499/321 native; both main
image restart and native benchmark steps pass in both CI runs. Six nested
dependency reports contain zero known advisories. Later deadline source is separate.

Source `17d4197` persists the common policy clock and per-occurrence task deadline
anchors. Internal decisions and their invocation index commit atomically with
ownership fencing, without a pending external intent. The two adapters retain
opaque receipt references in their own formats, accepting the exact root-clock
identity only for a timed plan. Fresh native recovery reuses original timestamps:
expired workflow/task budgets stop before another call, and remaining budgets
continue. Deleted/changed root clocks or task anchors reject before invocation
mutation or dispatch. Legacy nonterminal timed records without a policy clock
fail closed; no silent budget renewal or migration guarantee is introduced.
Both expired outcomes incorrectly complete on archived `cbb06b2` native LangGraph
source. Current focus passes 49/61 per SDK, including eight actual SQLite deadline
cases per principal. Broader recovery/admission/receipt/lifecycle regression passes
124/167 per SDK. Core passes 66/2, including atomic rollback, immutable decision
replay and stale-owner guards. Thirteen real collection guards select eight new
PostgreSQL deadline cases per principal adapter; PostgreSQL atomic storage and
changed-source full/configured acceptance remain required. Strict 68-source/fixture
typing and full scoped lint/formatting pass. The existing in-flight-key fixture
now gives one second for dispatch after clock/native storage, with a fake response
stalled for thirty seconds; a one-millisecond budget expired before dispatch.
Production deadline enforcement is unchanged by that fixture correction.

Durable retry clock/count/schedule and stable jitter choices, complete scoped
exceptions, real process-stop timing boundaries and full S2-04 acceptance remain
open. The task stays unchecked; later migration and proposed/JS owners remain.

Deadline artifact `fdd771e` fails four configured runs: stale-owner fixture
counts include the new completed clock row, and imported timeout dispatch does
not begin before a ten-millisecond Windows budget. One-call and unchanged-parent
checks still hold. Three runs succeed; this is not an all-seven-success artifact.
Source `a0ee9c0` corrects those fixture assumptions and completes immutable retry
clock/schedule/attempt choices, stable jitter replay and remaining-delay recovery.
Expired admitted retries reconcile known outcomes without another external call;
missing policy decisions or pending waits cannot start new work. Pending external
intents remain unresolved. Schedule arithmetic is checked before admission.
Standard OWS type/status/instance fields and inner/outer catch order pass new
fixtures, including handler/predicate failures. Common API shape is unchanged.

Focused recovery passes 46/52, fixture regression 75/34 and scoped-error/retry
checks 15/15 per principal environment. Actual OS process-stop tests pass 3/9 per
SDK: a killed worker bypasses cleanup, and a distinct process waits real test-lease
expiry before checking original deadlines and the saved jitter choice. Full frozen
local acceptance passes: core 1,732/728/three tracked gaps at 91.61%, ADK 985/579,
LangGraph 1,027/552 and optional Agent Framework 1,277/820, each with three gaps.
Six knowledge-extra checks separately pass. Strict 68-source/fixture typing,
215-file scoped lint/formatting, five wheel/sdist builds and current Python/pinned
schema wheel bytes pass against the frozen source manifest. The finite
[S2-04 audit](../s2-04-retry-timeout-acceptance.md) remains pending configured
acceptance: actual PostgreSQL/storage/process cases, Windows, image restarts,
native benchmarks, docs and six nested dependency reports must qualify this
source before the task is checked. Later migration/pending resolution and full
proposed/JavaScript owners remain unchanged.

A subsequent `3fb1041` push Windows job fails the imported descriptor/data-flow
fixture: its one-second budget expires during real adapter/storage work. This is
a separate success-path fixture, not an expiry assertion. Test-only correction
`b7d8674` raises its bounded budget to thirty seconds and updates the expected
resolved descriptor. Dedicated timeout controls and all production bytes remain
unchanged from the fully verified `a0ee9c0` implementation. Catalog checks pass
41 per root/ADK/LangGraph environment; the complete local Windows root check
passes 460/12. Exact configured qualification of this correction is still required.

Final prior artifact `3fb1041` has five successful configured runs and two
Windows failures. PostgreSQL actually passes 255 LangGraph, 221 ADK and 147
optional Agent Framework cases (seven optional skips), plus 122 storage and
10 native checkpoint checks. Its selected policy cases include five provider,
eleven timing, eight deadline, five retry-schedule and three actual process-stop
cases per principal, and atomic policy rollback. Both PostgreSQL images and
both main push/PR image pairs and native benchmarks pass. All six nested
security reports have zero known advisories. These passes do not waive Windows.

Test-only `29eeb3b` also corrects the PR Windows deadline fixture allowance while
strengthening its restored-budget assertion: a thirty-second original deadline
minus ten elapsed seconds must actually pass twenty seconds to the timer; after
thirty-one elapsed seconds it must pass zero and make no model call. Recorded
anchor bytes and corrupt-record admission assertions remain unchanged. The
native ADK catalog/deadline focus passes 49/24, and native LangGraph deadline
focus passes 8/24 with catalog 41 passing. The earlier ADK deadline command
without its native extra skipped all cases and is not qualification evidence.
All production and pinned schema bytes still match full-local source `a0ee9c0`;
only the two fixtures differ. Fresh exact-source configured acceptance is open.

### S2-03 checkpoint recovery — 2026-10-12 (verified)

Fresh-runtime tests reproduce a common resume defect: a submitted JSON null was
treated as absent input, leaving a recovered wait asleep. The common lifecycle
now checks whether an input was submitted separately from its value. The shared
fixture verifies null, false, zero, empty string, empty array and empty object,
including reopening services and duplicate terminal resume.

Targeted local validation passes 31 root and independent native ADK tests,
42 independent native LangGraph tests with seven database skips, and 46 optional
Agent Framework tests. These counts include the existing lifecycle suite; the
LangGraph run also includes native task checkpoint tests. Frozen-source broad
validation passes 1,244 root tests with 61 skips, three known expected gap
failures and 91.76% coverage. Independent native/shared/CTK suites pass ADK 611
(26 skips), LangGraph 624 (33 skips) and optional Agent Framework 821 (39 skips),
with the same three expected gap failures. Formatting, lint, strict typing, all
four locks, all five builds, documentation links and six inventory/source checks
pass. All exact `f2943c6` GitHub gates passed: [CI
38155232592](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155232592),
[push CI 38155229075](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155229075),
[PostgreSQL 38155232567](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155232567),
[external sandbox 38155232510](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155232510),
[Agent Framework 38155232603](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155232603),
[security 38155232528](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155232528)
and [documentation 38155232566](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38155232566).

The follow-up at `51f7259` passes broad local verification. A private common input table
retains the first raw input by invocation and fingerprint, separately from
resume messages. Terminal status, output and common error are written together
before adapter finalization. In-memory handles change only after a successful
store commit. Completed results and structured errors survive interruption
before native finalization, and late adapter values/faults preserve committed
terminal outcomes. The native ADK resume callback records completion separately
from its JSON result so successful null does not trigger a second execution.
Deterministic late-native-result probes preserve committed cancellation.

The shared recovery matrix now covers SQLite and configured PostgreSQL, all
six empty/false resume values against null/false/object/array original inputs,
workflow input transformation, duplicate terminal resume, successful null
completion, and value/null/fault finalization boundaries. Store fixtures prove
first binding across connections/reopening, rejected non-JSON data, definition
identity, corrupt records and a failed SQLite commit. Frozen-source verification
passes 1,319 root tests with 136 skips, three known expected gap failures and
91.73% coverage. Independent native/shared/CTK suites pass ADK 671 (98 skips),
LangGraph 684 (105 skips) and optional Agent Framework 909 (149 skips), each with
the same three expected gap failures. Formatting, lint, strict typing, all locks,
five builds, documentation links and six inventory/source checks pass. These
additions passed all exact gates at `6840605`: [CI
38156628780](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156628780),
[push CI 38156626150](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156626150),
[PostgreSQL 38156628821](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156628821),
[external sandbox 38156628799](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156628799),
[Agent Framework 38156628812](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156628812),
[security 38156628794](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156628794)
and [documentation 38156628828](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38156628828).
Linux root CI passed 1,323 tests with 132 skips, three expected gap failures and
91.75% coverage; shared contracts passed 569 with 98 skips and three expected
failures. The actual configured PostgreSQL matrix passed 36 new invocation
recovery cases plus 13 prior fork cases on each principal adapter. The optional
common bridge passed 34 new cases plus 13 prior fork cases, with two explicit
native-boundary skips. Both PostgreSQL runtime images passed restart acceptance.
See [recovery boundaries](../checkpoint-recovery.md).

The next action-receipt foundation passes broad local verification. RuntimeServices
composes a private journal on the same SQLite or PostgreSQL datasource. It
separates pending intents from completed null/value/fault receipts, retains the
first execution owner and outcome through conditional writes, and rejects
changed requests or completion by another owner. Identity includes invocation,
workflow fingerprint, task call site, execution occurrences and operation kind.
Request digests avoid storing request bodies in this journal. Thirty-four
focused cases cover concurrent connections, reopening, malformed data and
errors, owner mismatch, repeated execution identities and failed SQLite commit.
Three corresponding PostgreSQL store cases passed in the configured gate.
Frozen-source validation passes 1,353 root tests with 139 skips, three expected
gap failures and 91.78% coverage. Independent native/shared/CTK suites pass ADK
671 (98 skips), LangGraph 684 (105 skips) and optional Agent Framework 909
(149 skips), retaining the same three expected gaps. Formatting, lint, strict
typing, four dependency locks, five builds, documentation links and six
audit/source-preservation checks pass. All exact `d197fa1` gates passed: [CI
38158230755](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158230755),
[push CI 38158225706](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158225706),
[PostgreSQL 38158230768](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158230768),
[external sandbox 38158230712](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158230712),
[Agent Framework 38158230893](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158230893),
[security 38158231039](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158231039)
and [documentation 38158230744](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38158230744).
Linux root CI passed 1,357 tests with 135 skips, three expected gap failures and
91.81% coverage. The actual PostgreSQL store suite passed all nine cases,
including the three new journal outcomes; all prior shared recovery and both
runtime-image restart gates passed. That acceptance covers storage composition.

The current follow-up connects leaf protocol/catalog calls and prepared imported
calls to that journal. Completed value/null/fault outcomes are reused before
native task commit; repeated reusable calls retain distinct private occurrence
identities. A changed prepared request or an unresolved intent faults recovery
without dispatching the action again. These recovery failures bypass ordinary
task/catch retry routing. Receipt-write failures retain the pending intent.
Competitive forks drain siblings and propagate uncertainty without recording
a winner. Restart before any action intent can still choose a new winner.
Public task references and operation keys retain their current meanings.

Deterministic probes reproduced eight action replay failures before integration,
two unsafe catch retries before their guard, and four uncertain fork/retry
failures before the fork guard. Shared fixtures include configured PostgreSQL
and actual independent SDK environments. Frozen-source full local validation
passes 1,377 root tests with 163 skips, three expected gap failures and 91.79%
coverage. Independent native/shared/CTK suites pass ADK 695 (122 skips),
LangGraph 708 (129 skips) and optional Agent Framework 945 (185 skips), each
with the same three expected gaps. Focused recovery/fork/benchmark runs pass
82 root, 84 native ADK, 83 native LangGraph and 120 optional bridge cases.
Formatting, lint, strict typing, four locks, five builds, documentation links
and six audit/source checks pass. At `f63b801`, both CI runs, PostgreSQL,
external sandbox, optional Agent Framework and documentation passed. The
configured PostgreSQL suite passed nine store cases, native LangGraph
checkpoint tests passed 10 with eight skips, and shared recovery passed 61
on each principal adapter and 59 with two native-boundary skips on the optional
bridge. Security failed; the separate correction and full leaf-call acceptance
passed at `fc53574`, as recorded above.
Executable run, event and child operations are not
covered by this leaf-call follow-up; broader effect reconciliation remains open.

The next source at `31f64ac` adds conditional common invocation writes. Each
write compares its loaded durable snapshot and a private generation. A
running/waiting/running cycle cannot restore an old writer's authority. Stale
writes leave the newer record intact, and duplicate terminal writes retain the
first null/value/error. A late native result returns another worker's committed
terminal outcome; stale resume reads completion before dispatch. Loaded operator
handles coordinate with the original in-process handle, including a terminal
commit during refresh. Changed active state stops execution without adopting a
new writer's authority, entering retry/catch or establishing a fork winner.
A state conflict during dispatch leaves its uncertain action intent pending.
Common identity fields remain immutable; corrupt metadata has sanitized errors.
Legacy rows without the write generation or creator field remain readable,
without adding an original-input migration guarantee.

Before these guards, deterministic probes reproduced 13 common-store failures,
20 cross-adapter state failures, and four retry/fork propagation failures.
Frozen-source full local validation passes 1,461 root tests, 202 skips, three
expected gaps and 91.84% coverage. Independent native/shared/CTK suites pass
ADK 729 (156 skips), LangGraph 742 (163 skips) and optional Agent Framework
996 (236 skips), each with the same three gaps. Focused store/lifecycle/worker
tests pass 78 with 34 unconfigured database skips. Formatting, lint, strict
typing, four locks, five builds and documentation checks pass. Five new
PostgreSQL store cases and the same shared worker assertions are wired into the
configured gate and passed at `6954db8`. All seven exact gates passed: [PR
CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163682555),
[push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163678738),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163682598),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163682554),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163682536),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163682524)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38163682518).
Actual Linux root validation passed 1,465 tests, 198 skips, three expected gaps
and 91.87% coverage. Configured PostgreSQL passed 14 common-store cases,
10 native LangGraph cases with eight skips, and 78 shared cases on each
principal adapter; the optional bridge passed 76 with two native-boundary
skips. Both PostgreSQL runtime-image restart jobs passed. The six downloaded
Linux audit reports contain zero known advisories.
Conditional metadata writes are not an exclusive execution lease or an atomic
fence around native checkpoint writes.

New source `58516fb` reads the existing original-input binding without inserting
resume data. Before changing common status, principal native recovery checks
that the input agrees with an existing ADK invocation event or LangGraph root
checkpoint. Missing/deleted native records, missing/changed original input and
unavailable installed native persistence reject admission without dispatch or
a new common state. ADK now selects input for each invocation rather than
session-wide input, preserving two invocations that share a session. Native
admission storage/cleanup failures have sanitized common errors.

Before these changes, eight principal SDK admission cases failed and a shared
ADK session recovered the first invocation's input for its second invocation.
A separate cleanup probe showed a framework error escaping the common contract.
Frozen-source full local validation passed 1,473 root tests, 230 skips, three
expected gaps and 91.83% coverage. Independent native/shared/CTK suites passed
ADK 739 (173 skips), LangGraph 749 (180 skips) and optional Agent Framework
999 (269 skips), each with the same three gaps. Formatting, lint, strict typing,
four locks, five builds and documentation checks pass. All seven GitHub gates
passed at `b9a2b31`, including configured PostgreSQL admission fixtures.
The Linux root passed 1,477 tests (226 skips, three gaps, 91.86% coverage);
PostgreSQL passed 20 store cases, 84 shared cases per principal adapter and
77 on the optional bridge (seven native-admission/boundary skips). Both packaged
PostgreSQL restart jobs passed, and all six security reports had zero known
advisories. These are
initial input-binding admission checks, not complete nested checkpoint
validation, an execution lease or atomic native checkpoint fencing.

Test-only source `68722d3` adds actual forced OS process-stop fixtures. At eight
selected boundaries, an owning test kills its runtime worker without graceful
cleanup, then starts a distinct process against the same persisted state.
SQLite proof passed eight cases independently in ADK and LangGraph and eight
on the optional Agent Framework common bridge (24/24/40 explicit skips).
The fixtures preserve original input, null/value/fault outcomes, unchanged
action receipts and duplicate terminal results. A separate fake external store
has no deduplication: its call count confirms that recovery does not repeat the
action, including an unresolved pending receipt. The optional bridge results do
not qualify Agent Framework native checkpoint durability. All seven gates
passed at `ef29f23`, including the matching PostgreSQL and Windows fixtures.
PostgreSQL passed 92 shared cases per principal adapter and 85 on the optional
bridge (seven skips), with both packaged runtime restarts successful. The
Windows native job passed 255 cases (31 explicit skips). Linux root passed
1,477 (258 skips, three gaps, 91.86%); all six security reports had zero known
advisories.
Strict fixture typing, formatting, lint and audit checks passed. Full-suite
counts above retain the unchanged runtime source `58516fb`; these new focused
cases are separate proof. Earlier connection errors have been resolved; the
input-admission and forced-process source gates are now confirmed complete.

Private ownership storage at `75fbc0d` uses the common datasource's clock for
atomic expiring claims, renewal and release, retaining generation tokens across
reacquisition. Independent connections reject competing or stale owners, host
clock skew cannot grant ownership, and failed SQLite commits retain the previous
record. A corrupt infinite-expiry probe exposed unsafe renewal in the first
foundation; the corrected guard rejects verify/renew/release without repairing
the record. RuntimeServices composes and closes this separate common service.
This is storage foundation: invocation/resume admission, heartbeat supervision
and atomic common/native write fencing do not use it yet. A deterministic
two-runtime probe confirms that both still enter the same invocation.

Corrected-source full root validation passed 1,498 tests, 274 skips and three
expected gaps at 91.89% coverage. Its focused ownership suite passed 25 with
12 unconfigured PostgreSQL skips. The independent native/shared/CTK regression
baseline at `f00a7a0` passed ADK 747 (197 skips), LangGraph 757 (204 skips) and
optional Agent Framework 1,007 (309 skips), each with three expected gaps.
Adapters and service composition are unchanged by the subsequent unused-store
expiry guard and fixture typing correction. Formatting, lint, strict package/CI
and fixture typing, four locks, five builds and artifact checks pass. Fresh
GitHub/configured PostgreSQL acceptance for the storage foundation subsequently
passed all seven gates at `cdd6e17`. Linux root passed 1,502 cases (270 skips,
three gaps, 91.91% coverage); PostgreSQL passed 57 common-store cases and both
packaged runtime restarts. Its six downloaded dependency reports contain no
known advisories. The earlier two-runtime negative probe remains scoped to the
storage-only source.

Runtime source `b2dfc5c` integrates private invocation/resume ownership,
in-process caller coalescing, renewal, lease-loss cancellation and shutdown
draining. Common state writes and actual native SDK checkpoint mutations lock
the common ownership row through their storage transaction. SQLite TEMP
triggers and PostgreSQL transaction guards preserve SDK-owned formats and SQL.
Native storage workers drain before connections close, including repeated
cancellation; workflow execution remains on its engine.

The corrected frozen local regression passes 1,532 root cases (304 skips,
three gaps, 91.85% coverage), ADK 771 (219 skips), LangGraph 781 (226 skips)
and optional Agent Framework 1,037 (340 skips), each with three gaps.
These full local runs precede the final PostgreSQL-only ADK schema-read
binding adjustment; its final SQLite focus passes 33 cases with 22 database
skips. Exact `b2dfc5c` configured PostgreSQL proves the final adjustment:
65 common-store cases, 101 shared cases per principal SDK, 90 optional bridge
cases (seven skips), 10 separate LangGraph cases (eight skips), and both
packaged PostgreSQL restart jobs pass. Linux root passes 1,536 (300 skips,
three gaps, 91.87%); native suites pass ADK 675 and LangGraph 685 plus 96 CTK
cases each. Windows passes 452 root cases (four skips) and 279 native cases
(53 skips). All six fresh dependency reports have zero known advisories.

Five exact source gates pass: [PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172139241),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172139245),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172139233),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172139248)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172139237).
Both [PR CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172139235)
and [push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38172134981)
fail LangGraph's immediate forced-stop image resume with HTTP 409 while the
killed owner's lease remains live; ADK image jobs are cancelled by fail-fast.
Their root, Windows, native and CTK jobs pass. The acceptance correction waits
for actual production lease expiry and rejects every other error; its fresh
image proof remains pending. Action intent and fork progress guards are also
under validation. Original dispatch owners retain truthful outcome receipts
after expiry, without gaining authority to start another action.

The follow-up now locks execution authority through action intent and fork
winner/drain commits, using an explicit PostgreSQL transaction or a SQLite
writer lock. Existing intent replay is also guarded. Original dispatch owners
can still retain completed external-action outcomes after expiry. Frozen local
regression passes 1,544 root tests (316 skips, three gaps, 91.81% coverage),
ADK 775 (223 skips), LangGraph 785 (230 skips) and optional Agent Framework
1,043 (346 skips), each retaining three gaps. Formatting, lint, strict typing,
all locks, five package builds, current-source wheel/schema checks and
audit/documentation checks pass. The four actual restart-poll harness scenarios
prove eventual success, unrelated conflict and server-error rejection, and a
bounded failure when ownership never expires. All seven exact `2acf632` gates
passed: [PR CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173674509),
[push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173670848),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173674499),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173674571),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173674497),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173674526)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38173674506).
Both main CI Docker stop/restart/resume steps pass with actual production lease
expiry. PostgreSQL passes 81 common-store cases, 103 per principal SDK, 92 optional
bridge cases (seven skips), 10 separate LangGraph cases (eight skips), and both
runtime images. Linux root passes 1,548 (312 skips, three gaps, 91.83% coverage);
native suites pass ADK 679 and LangGraph 689 plus 96 CTK each. Windows passes 460
root (12 skips) and 283 native cases (57 skips). All six actual downloaded
dependency reports contain zero known advisories across 71/109/96/74 runtime
and 13/16 controller dependencies. Historical `b2dfc5c` image failures remain
recorded with their source scope.

Two actual SQLite root/nested corruption probes on the prior LangGraph code
fail to reject malformed outputs. Source `556da47` validates SDK-owned snapshots
before common resume admission and before changing live execution state. It
checks finite JSON with unique keys, required fields/control types, task and
scope identities, self occurrence, execution paths, original input, workflow
definition and context binding. Read-only SDK traversal validates stored nested
occurrences while preserving engine-owned serialization and checkpoint SQL.
The shared fixture passes 24 actual SQLite cases, including clean recovery and
eleven corruptions per root/nested scope. Full independent LangGraph
native/shared/CTK regression passes 809 (254 skips, three tracked gaps).
Formatting, lint, strict package/fixture typing and the LangGraph wheel/sdist
build pass; wheel bytes match current adapter code. Fresh GitHub/configured
PostgreSQL acceptance for this source initially failed at `fc54600`: all 22
corruption cases update zero blob rows because the installed SDK inlines these
primitive JSON-text channels in checkpoint JSONB. The two clean cases and 103
existing shared cases pass; subsequent ADK/optional and PostgreSQL image steps
do not run. Six other exact gates pass, including both main CI forced-stop
images. Linux root passes 1,548 (360 skips, three gaps, 91.83%); LangGraph passes
713 plus 96 CTK (254 skips), and Windows native passes 307 (81 skips). All six
downloaded dependency reports contain zero known advisories. These failed
mutation probes do not qualify corrupted PostgreSQL checkpoint recovery.

Source `005ab5b` corrects the fixture to mutate the SDK's inline JSONB channel
and re-read the exact checkpoint to verify the altered value. The SQLite
recheck passes 24 cases with 24 unconfigured database skips. A separate actual
ADK probe reproduces acceptance of a contradictory later input event for the
same native invocation. The adapter now validates every such input, rejects
malformed or duplicate-key JSON and retains matching replay messages. Shared
session invocations remain separate. The focused recovery run passes 12 cases
with 22 skips; full independent ADK native/shared/CTK regression passes 780
(276 skips, three tracked gaps). Formatting, lint, strict package/two-fixture
typing and ADK wheel/sdist build/source checks pass. LangGraph runtime is
unchanged from `556da47`; its full 809-case local baseline remains source-scoped.
All seven exact `4058895` gates pass: [PR CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175692483),
[push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175689112),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175692473),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175692651),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175692469),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175692465)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38175692474).
PostgreSQL passes 127 LangGraph and 108 ADK shared recovery cases, 81 common
store cases, 92 optional bridge cases (seven skips), 10 separate LangGraph
cases (eight skips), and both runtime images. Both main CI forced-stop restart
steps pass. Linux root passes 1,548 (370 skips, three gaps, 91.83%); native ADK
684 and LangGraph 713 plus 96 CTK each. Windows root/native pass 460/12 and
307/81; six actual downloaded dependency reports contain zero known advisories.
Historical `fc54600` failed mutations remain recorded with their source scope.

Six additional actual SQLite root/nested probes reproduce recovery acceptance
of Boolean stop/exit/end flags that disagree with pending task routing. Source
`e61c6a5` rejects that inconsistency and routes native completion directly to END
when workflow end is already set. The focused suite passes 41 (37 skips), and
the full independent LangGraph native/shared/CTK regression passes 815
(270 skips, three gaps). Formatting, lint, strict package/fixture typing and
LangGraph wheel/sdist/source-byte checks pass. Actual `6af95cf` routing
acceptance and its separate PR benchmark failure are recorded below.

Historical actual SDK probes at `e61c6a5` demonstrate a native/common receipt gap:
after a checkpoint passes the first fake HTTP call and waits, deleting or changing
its common completed receipt still permits recovery. Both cases complete and
dispatch the next call, with history `/once`, `/next`, while changing the common
invocation. They do not repeat `/once`, but they accept disagreement with its
known durable receipt. Checkpoint dependencies on common receipts must be
verified before recovery admission. Missing non-self cursor and payload/common receipt
consistency remain open. A cancelled child can legitimately lack its first
checkpoint, so a declared cursor alone does not establish corruption.

At `6af95cf`, six exact gates succeed: push CI, PostgreSQL, external sandbox,
optional Agent Framework, Security and Docs. [PR CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38176500015)
passes ADK native/shared and CTK steps, then fails its 10-iteration/concurrency-4
ADK benchmark while storing common invocation status. Its LangGraph native job
is cancelled and its image jobs are skipped. This revision is not all-gates green.
The separate [push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38176495800)
passes both actual forced-stop restart steps with production lease expiry.
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38176500068)
passes 133 LangGraph/108 ADK shared recovery cases, 81 common-store cases,
92 optional bridge cases with seven skips, 10 separate native LangGraph cases
with eight skips, and both runtime images. Linux root passes 1,548/382/three
gaps/91.83%; native ADK 684 and LangGraph 719 plus 96 CTK each; Windows root/native
460/12 and 313/87; optional Agent Framework 1,043/416/three gaps. All six actual
downloaded [Security reports](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38176500028)
contain zero known advisories. Raw historical acceptance snapshots remain unchanged.

Source `9755e96` records immutable common receipt references inside SDK-owned
state. LangGraph checks a snapshot/counters/routing checksum and receipt
references; ADK retains sequenced native metadata events and awaits receipt/wait
persistence before advancing common state. Recovery rejects missing, changed or
pending receipts before another fake external call or common invocation mutation.
Identical shared root/nested value/null/fault cases pass on both engines. Their
missing-receipt assertion fails against exact prior `6af95cf` source on both SDKs.
A valid waiting fork followed by a sibling receipt remains recoverable; deleting
that sibling receipt rejects recovery. Native state and SQL remain SDK-owned.
The digests establish consistency, not checkpoint authentication or exactly-once
external actions. Configured PostgreSQL cases are wired and await fresh acceptance.

The ADK SQLite reader runs through the existing storage worker. A controlled
actual SQLite read/fenced SDK commit reproduces the old lock cycle: a synchronous
common write fails with the reader on the main loop and succeeds with the worker.
Both assertions pass. The corrected actual native benchmark passes ten iterations
at concurrency four and interruption recovery executes its external action once.
The prior CI failure remains recorded; this local result does not qualify fresh CI.

Frozen regression passes core 1,544/484/three tracked gaps with 91.46% coverage,
LangGraph native/shared/CTK 839/342/three gaps and optional Agent Framework
1,043/512/three gaps. Initial ADK native/shared/CTK passes 806/360/three gaps;
after the ADK waiting-history correction and added fork fixture, its final full
rerun passes 808/366/three gaps. Unchanged LangGraph passes the new fork focus
2/6; final ADK receipt/fork/reader focus passes 28/78. Core and other adapters
remain identical to their frozen full-regression sources. Formatting, lint,
strict 66-package-source and three-fixture typing, five wheel/sdist builds and
current wheel Python/pinned-schema bytes pass. ADK is rebuilt after its final
condition change. Optional Agent Framework remains qualified only through its
common bridge. Fresh exact-source GitHub/Windows/configured PostgreSQL acceptance
and complete native/common invocation/payload/cursor reconciliation remain open.

All seven exact `c276f40` receipt/native-reader gates pass:
[PR CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179200484),
[push CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179197842),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179200384),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179200475),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179200516),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179200396)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38179200495).
Both main CI runs pass both forced-stop image restarts with production lease
expiry and both native benchmark probes. PostgreSQL passes 159 LangGraph/134
ADK shared recovery cases, 81 common-store cases, 92 optional bridge cases with
seven skips, 10 separate LangGraph cases with eight skips, and both runtime
images. Linux root passes 1,548/488/three gaps/91.49%; native ADK 712 and
LangGraph 745 plus 96 CTK each; Windows root/native pass 460/12 and 339/165;
optional Agent Framework passes 1,043/520/three gaps. All six actual downloaded
dependency reports contain zero known advisories. The earlier `6af95cf` PR
benchmark failure remains recorded. These results qualify the receipt/native-reader
source, and do not qualify subsequent scope dependency changes.

An actual SDK probe at `c276f40` still accepts deletion of a completed nested
scope checkpoint, dispatches the next fake HTTP call and changes common state.
Source `968ec8f` retains immutable completed-scope dependencies in later native
state and verifies their presence, terminal routing and digest before admission.
Its consistency checksum includes receipt/scope references. Two prior-source
assertions fail for a deleted checkpoint and for an internally consistent child
payload/checksum change. Both now reject recovery without dispatch or common
mutation. Nested scopes and earlier loop occurrences are covered. A cancelled
competing child may retain a cursor without its first checkpoint; the fixture
proves that legitimate absence remains valid while its completed winner stays
required. Checkpoint state and SQL remain engine-owned.

The focus passes 65/117 and full LangGraph native/shared/CTK regression passes
850/357/three tracked gaps. Formatting, lint, strict package/fixture typing,
LangGraph wheel/sdist and current wheel-source/pinned-schema checks pass on frozen
source. Core, ADK and optional adapters remain unchanged from their separately
qualified receipt/native-reader source. Private binding version 2 introduces no
old-checkpoint migration guarantee. Fresh configured/remote acceptance and complete
native/common invocation/payload consistency remain open.

All seven `ac478e1` jobs succeed. Actual PostgreSQL logs show 159 LangGraph and
134 ADK shared cases, but the original keyword filter deselects all nine new
completed-scope cases. Those jobs do not qualify PostgreSQL scope consistency.
Source `3df61f6` corrects the actual selection expression. Three collection
checks prove that the configured commands select nine completed-scope cases and
eighteen sandbox recovery cases for each principal SDK.

The same source adds private common receipts around existing sandbox dispatch.
An interruption after a completed sandbox operation but before native task
progress previously dispatched it twice; the prior-source assertion fails on
the actual count. Script, shell and container value/fault receipts now retain
one dispatch across fresh recovery; uncertain intents and changed prepared
requests reject another dispatch. Execution identity remains stable per private
occurrence, while explicit retries keep distinct attempts. Dispatch still uses
SandboxManager. Deployment policy, capabilities and enforced isolation remain
unchanged. Deterministic fake backends qualify recovery behavior, not stronger
isolation or real-cluster deployment acceptance.

All four full regressions pass on frozen runtime/test source: core 1,583/546/
three tracked gaps with 91.50% coverage, ADK 844/420, LangGraph 886/393 and
optional Agent Framework 1,097/592, each with three tracked gaps. Both principal
SDK focus suites pass 49/36. Formatting, lint, strict package/fixture/CI typing,
five wheel/sdist builds and current wheel-source/pinned-schema bytes pass.
All seven exact `c7b70ad` jobs succeed. The actual corrected PostgreSQL command
executes and passes all nine scope cases and eighteen sandbox cases per adapter:
shared recovery passes 186 LangGraph, 152 ADK and 110 optional Agent Framework
cases (seven optional skips). Both PostgreSQL runtime images and both main CI
forced-stop image restarts pass. Both native benchmark steps pass in both main
CI runs. Linux root passes 1,587/542/three tracked gaps with 91.52% coverage;
shared contracts 689/468/three gaps; native LangGraph 790/393 and ADK 748/420,
each with three gaps and 96 additional CTK passes. Windows root/native pass
460/12 and 384/210; optional native/shared/CTK passes 1,097/592/three gaps.
All six actual downloaded dependency reports contain zero known advisories.
These results qualify the sandbox and corrected scope selection source; they
do not qualify subsequent event/child changes.

Source `5506277` additionally wraps event publication and existing synchronous
child calls in the common journal. Prepared properties/default source and
resolved child fingerprint/input bind their requests. Value/null/fault outcomes
are reused; changed requests or child definitions reject redispatch and
uncertain intents remain unresolved. The actual prior interruption assertions
fail because both operations execute twice. Focus passes 70/64 in each
principal SDK environment, including existing event/listener, separate child
identity and leaf-call contracts. Five collection guards verify twenty
configured event/child cases per principal adapter along with the existing
scope/sandbox selection. Full frozen regressions pass: core
1,625/586/three tracked gaps with 91.58% coverage;
ADK 884/460, LangGraph 926/433 and optional Agent Framework 1,157/652,
each with three tracked gaps.
Formatting, lint, strict package/fixture/CI typing, five wheel/sdist builds,
current wheel-source/pinned-schema bytes and unchanged-source checks pass.
Fresh remote acceptance remains pending for this source.

Actual probes on both principal SDKs delete the completed child's common
invocation record after the parent retains its receipt. Recovery still accepts
the known output. Child-record dependency reconciliation and complete
native/common consistency remain open. The event bus has no new durable
delivery guarantee; child scheduling/budget semantics retain S2-07 ownership.

All seven exact `33cd41f` jobs subsequently pass for the event/child receipt
source. Configured PostgreSQL executes twenty event/child cases per adapter:
shared recovery passes 206 LangGraph, 172 ADK and 130 optional bridge checks
(seven optional skips), with both runtime images green. Linux root passes
1,629/582/three tracked gaps with 91.61% coverage; shared contracts 729/508;
native ADK 788/460 and LangGraph 830/433, each with three gaps and 96 CTK passes.
Windows root/native pass 460/12 and 424/250; optional native/shared/CTK passes
1,157/652/three gaps. Both native benchmarks and actual forced-stop image
restart steps pass in both main CI runs. Six downloaded dependency reports
contain zero known advisories. These results qualify the preceding receipt
source, rather than subsequent child-record reconciliation.

Source `e39a7c0` binds known child outcomes to the terminal common record and
original input, and verifies the original prepared fingerprint/input and parent
identity. A common action identity index is admitted atomically with intents.
Read-only pre-admission checks traverse associated receipts and descendant
child actions, including the interval before native progress retains a receipt
link. Deleted or changed child records, input, execution references, descendant
receipts and index metadata reject before parent mutation or dispatch. Identical
fixtures yield the same common state error on both principal adapters, while
clean value/null/fault outcomes and duplicate terminal resume remain valid.
Focus passes 77/74 per principal SDK; storage/selection checks pass 52/9.
The same task-gap and waiting deletion assertions fail on exact archived
`5506277` source. Seven real collection guards select seventeen configured
child-dependency cases per principal adapter. A rollback injection checks
atomic index/intent storage; its PostgreSQL counterpart is in the configured
store suite. Existing non-child receipt digests retain their prior format;
older unbound child outcomes fail closed without a migration guarantee.
Full frozen regressions pass: core 1,664/621/three tracked gaps
with 91.67% coverage; ADK 918/494, LangGraph 960/467, optional Agent Framework 1,208/703,
each with three tracked gaps. Formatting, lint, strict package/fixture/CI typing,
five wheel/sdist builds and current wheel-source/pinned-schema bytes pass.
Fresh exact-source configured qualification remains pending.

The final S2-03 audit must verify common identity/fingerprint/original-input and
terminal-result recovery; native checkpoint/session and routing consistency;
known side-effect receipts and child-record dependencies; unresolved-intent
admission behavior; ownership/fencing and forced-stop recovery; identical
principal-engine assertions on SQLite/PostgreSQL; and current source quality,
build, Windows, benchmark, image and dependency acceptance. Full proposed/JS
criteria, event inbox/outbox, child scheduling/budgets, general pending-outcome
resolution, compensation and version migration retain their later owners.

The requirement audit is recorded in [S2-03 recovery acceptance](../s2-03-recovery-acceptance.md).
At `29c681d`, PR CI and the five other configured runs succeed. PostgreSQL
passes 119 store, 223 LangGraph, 189 ADK and 147 optional bridge checks (seven
optional skips), including all seventeen child-dependency cases per adapter
and the actual PostgreSQL index/intent rollback test. Both PostgreSQL images and
both main CI runtime image restart jobs pass. Push CI is cancelled at its
ten-minute Windows limit; its log records two process-stop failures before
cancellation. A two-second test lease is lost before terminal-null readiness;
terminal-fault signals readiness with a still-running record. This is not an
all-seven-success artifact.

The follow-up requires the terminal readiness marker to verify the expected
committed common outcome, changes only the test lease from two to five seconds,
and bounds the growing Windows suite at twenty minutes. Production ownership
settings remain unchanged. Local actual process-stop fixtures pass 8/24 per
principal SDK; current root/engine runtime source remains `e39a7c0`.
Final acceptance at `ddd194e` supersedes that pending follow-up: all seven
configured runs succeed. Both push/PR Windows suites pass 460 core/458 native
cases (12/284 skips), and both main CI image restart and native benchmark steps
pass in each run. Linux core passes 1,668/617/three tracked gaps at 91.69%
coverage; shared contracts pass 763/542/three gaps. Native ADK passes 822/494
and LangGraph 864/467, each with three tracked gaps plus 96 CTK cases. Optional
Agent Framework passes 1,208/703/three gaps. PostgreSQL passes 119 store, 223
LangGraph, 189 ADK and 147 optional bridge checks (seven optional skips), plus
both runtime images. Terminal logs confirm seventeen child-dependency, twenty
event/child and eighteen sandbox cases per adapter, all nine completed-scope
cases and the actual PostgreSQL atomic rollback test. Six fresh nested dependency
reports contain zero known advisories. The ten scoped recovery requirements in
the linked audit are verified, so S2-03 is complete. The earlier cancellation
and failures remain preserved; S2-04 is now verified and S2-05 is the next ready task.
General pending-effect resolution remains later-stage work.
Legacy rows without an original-input record
have no new migration guarantee. Later-stage retry/deadline,
timer/event, child workflow, compensation and version migration owners remain
unchanged; no stronger recovery or exactly-once guarantee is advertised.

### S2-02 competitive fork decisions — 2026-10-11 (verified)

The common runtime now composes an embedded decision store using its existing
SQLite or optional PostgreSQL datasource. An atomic claim retains the first
recorded winner and its loser cancellation intents across connections and
reopening; invocation, frozen workflow identity, resolved task call site and
execution occurrences separate races. Cleanup receipts remain separate from
winner data. Native checkpoint state stays engine-private.

The storage foundation at `7f82470` passed 1,173 local root tests with 35 skips,
three expected protocol/event failures and 91.67% coverage. Independent native
suites passed ADK 543, LangGraph 556 (seven PostgreSQL skips) and optional Agent
Framework 719 tests. All required exact foundation GitHub gates passed: [CI
38149999427](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149999427),
[PostgreSQL 38149999424](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149999424),
[external sandbox 38149999595](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149999595),
[Agent Framework 38149999413](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149999413),
[security 38149999431](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149999431),
and [documentation 38149999416](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149999416).

Executor integration is now under local verification. It records the first
observed branch completion before cancelling losers, including explicit null,
common errors and workflow end, and checks saved decisions before starting
branches on replay. Private occurrence coordinates distinguish loop, reusable,
nested and catch-retry executions; LangGraph persists them in native snapshots.
The simultaneous-ready fixture reproduces declaration-order winner selection
on the prior implementation and passes with completion-time recording.
Shared fixtures exercise fresh-service restart before a cleanup receipt and
while loser cleanup is interrupted. The latter caught and corrected end-branch
cancellation before the LangGraph winner checkpoint had finished.

Configured PostgreSQL restart fixtures are now included for independent native
ADK and LangGraph environments. Fresh full local validation passes 1,218 root
tests with 51 skips, three expected gap failures and 91.72% coverage. Independent
native/shared/CTK suites pass ADK 585 (16 skips), LangGraph 598 (23 skips) and
optional Agent Framework 782 (24 skips), retaining the same three expected gap
failures. The competitive fixtures pass 42 principal/root executions and 63 in
the optional environment; local PostgreSQL cases await a configured database.
Seventeen store cases and the simultaneous-ready/common-error replay probes
pass. Interruption before any decision restarts with a new winner, and repeated
transitions retain distinct decisions on every adapter. All locks, five package
builds, formatting, lint, strict typing and documentation links pass.

All exact runtime GitHub gates at `2fbe548` passed: [CI
38152109920](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152109920),
[push CI 38152106466](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152106466),
[PostgreSQL 38152109956](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152109956),
[external sandbox 38152109950](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152109950),
[Agent Framework 38152109988](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152109988),
[security 38152110041](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152110041),
and [documentation 38152109917](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152109917).
The configured database job executes eight winner/null/fault/end restart cases
on each principal native adapter, including interruption during cleanup, and
both PostgreSQL runtime images pass restart acceptance. Linux root CI passes
1,222 tests with 47 skips, three expected failures and 91.76% coverage; shared
contracts pass 483 with 16 database skips. Its selected native jobs pass ADK 489
and LangGraph 502; optional Agent Framework passes 782.

Additional fixtures at `5dbc181` prove SQLite native recovery after a prior
completed occurrence in loops, reusable calls, nested races, transitions and
catch retries. An end originating outside a competitive scope drains it without
inventing a winner. The whole competitive file passes 56 principal/root cases
with 26 database skips, and 84 optional-adapter cases with 39 skips. The prior
full suites qualify unchanged runtime code; these additional fixtures pass
independently. The affected audit now records their exact source commit and
retains the earlier immutable receipts. All exact follow-up fixture/audit gates
at `51c204e` passed: [CI 38152858220](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152858220),
[push CI 38152854446](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152854446),
[PostgreSQL 38152858245](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152858245),
[external sandbox 38152858243](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152858243),
[optional adapter 38152858272](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152858272),
[security 38152858238](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152858238),
and [docs 38152858223](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38152858223).
Linux root passes 1,236 tests with 57 skips, three expected failures and 91.79%
coverage. Selected native jobs pass ADK 503 and LangGraph 516; optional Agent
Framework passes 803. The configured database job passes all 13 winner and
repeated-scope restart cases on each principal native adapter.

The optional package also exposes the common PostgreSQL binding. Its same 13
database bridge cases pass in the independent gate at `81d27b3`, making 39
configured cases across the three adapter environments. All exact final gates
pass: [CI 38153550708](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153550708),
[push CI 38153548023](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153548023),
[PostgreSQL 38153550655](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153550655),
[external sandbox 38153550759](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153550759),
[optional adapter 38153550700](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153550700),
[security 38153550667](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153550667),
and [docs 38153550675](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38153550675).
This common-binding acceptance does not activate production images or stronger
native checkpoint hardening. S2-02 is complete at the recorded scope.
These records do not establish
general pending-effect reconciliation, exactly-once external actions, durable
timers, native metadata extensions or full conformance. A separate observed
built-in error-status catch fallback gap was tracked for S2-04 and is corrected
in its `23b9d35` error-context block; broader S2-04 work remains open.

### S2-01 ordered fork results — 2026-10-11 (verified)

Noncompetitive fork returns the declaration-ordered array of all branch
outputs, retaining overlapping keys and JSON null/scalar/list/object values.
Original input is no longer implicitly merged. Existing assertions that
encoded merged output now follow the pinned OWS rule. Common output/export
can explicitly aggregate results or merge objects through jq; branch-local
exports remain isolated from siblings and parent context. See
[fork behavior and migration](../workflow-forks.md).

Local full validation passes 1,153 root tests with 30 skips, three expected
protocol/event failures and 91.67% coverage. Independent engine suites pass
539 ADK tests, 549 LangGraph tests (six unconfigured PostgreSQL skips), and
713 optional Agent Framework tests, each with three expected gap failures.
These full runs precede the additional failure/recovery fixtures, which pass
separately: 16 shared fork executions on each principal adapter, 24 on optional
Agent Framework, and 27 LangGraph fork/native executions with seven local
PostgreSQL skips. Two pinned native SDK checks also prove that default concurrent
same-key writes fail and an explicit append reducer retains both values; these
primitive checks do not implement public native metadata extensions.

The desired ordered-fork audit probe now passes without an expected-failure
marker. Branch failure drains siblings before returning the common error.
Real SQLite and configured PostgreSQL restart retain ordered branch outputs
without replaying completed calls. A further shared fixture at `ad33b4f` reopens
services and resumes interrupted forks on every adapter, verifying ordered
outputs and persisted completed results: 18 shared executions pass in each
principal environment, 27 in optional Agent Framework; the LangGraph environment
also passes both native collision/reducer primitive checks (20 total). This
shared restart case qualifies output shape; general pending-effect reconciliation
is S2-03/S2-08 work.

Formatting, lint, strict typing, all five locks, root/core builds and Markdown
links pass. Exact runtime acceptance is green at `6c84072` (runtime change
`5f97cb9`): root CI reports 1,159 passed, 30 skipped, three expected failures and
91.70% coverage; shared contracts report 439 passed and three expected failures.
Independent CI matrices report ADK 445, LangGraph 458 (seven PostgreSQL skips),
and optional Agent Framework 716 passed. Both runtime images, SQLite defaults,
PostgreSQL containers, restricted external sandbox, Windows, manifests, mutation,
security and docs passed. The later shared test changes no runtime code and is
separately qualified locally. All seven follow-up GitHub runs also passed at
`a83a474`: CI [38149279907](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149279907),
push CI [38149276892](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149276892),
external sandbox [38149279962](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149279962),
PostgreSQL [38149279918](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149279918),
optional Agent Framework [38149279905](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149279905),
security [38149279877](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149279877)
and docs [38149279876](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38149279876).

The current audit reviews `ad33b4f`, recording seven fork features as qualified
subsets (178 qualified subsets, 152 partial outcomes and 32 mismatches). It
retains historical S1-08 evidence, all complete proposed criteria and independent
JS gaps. Public native channels remain unimplemented despite SDK primitive checks;
their versioned metadata implementation belongs to S4-01/S4-02.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38148408731](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148408731) | success |
| CI (push) | [38148405147](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148405147) | success |
| External Sandbox CI | [38148408720](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148408720) | success |
| PostgreSQL CI | [38148408705](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148408705) | success |
| Agent Framework CI (optional) | [38148408693](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148408693) | success |
| Security | [38148408820](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148408820) | success |
| Docs | [38148408742](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38148408742) | success |

### S1-08 current implementation audit — 2026-10-11 (verified)

The [implementation audit](../current-implementation-audit.md) covers all
658 tracked feature IDs against active code and actual assertion scope, with
77 source/test evidence groups and independent language outcomes. It preserves
all original research fields, 443 proposed full acceptance tests and 774
structural constraints. It records 171 qualified subsets, 156 partial outcomes,
35 behavior mismatches, 197 parent/admission rejections, 27 inert fields,
9 private native uses and 63 unimplemented public features. These are audit
outcomes, not full feature-completion counts. Independent JS remains absent.

Four audit-integrity and two consolidation-integrity checks pass. Gap probes
pass six admission checks and reproduce five expected assertion failures in
the root and independent ADK/LangGraph environments; optional Agent Framework
reproduces six failures including its own fork result. Unmarked execution
confirms failures at ordered fork output, OpenAPI request routing, HTTP binary
raw output and null event-data assertions. Strict AssertionError-only xfails
retain S2-01/S2-05/S3-03 repair owners and never count as implemented features.
Ruff, formatting and Markdown links pass. Exact audit acceptance is green at
`a1cbae83e2a973e48be1afa893d1bac24fd7b8ac`: CI root reports 1141 passed,
26 skipped, 5 expected failures and 91.69% coverage; shared contracts report
421 passed and 5 expected failures. Both native/CTK and packaged runtime images,
Windows, mutation, manifests, PostgreSQL, external sandbox, optional Agent
Framework, security and docs passed. The reviewed runtime baseline remains
`f7be5d1`; no runtime capability changed in the audit.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38146714092](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146714092) | success |
| CI (push) | [38146711017](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146711017) | success |
| External Sandbox CI | [38146714047](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146714047) | success |
| PostgreSQL CI | [38146714069](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146714069) | success |
| Agent Framework CI (optional) | [38146714162](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146714162) | success |
| Security | [38146714020](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146714020) | success |
| Docs | [38146714075](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38146714075) | success |

### S1-07 reusable definitions and resource identity — 2026-10-11 (verified)

Core resolves reusable task functions, named errors, catch retry policies and
task/workflow timeouts into separate immutable execution definitions. Original
workflow/task definitions and escaped pointers remain available to descriptors.
Reusable task pipelines preserve parameter data, nested scope behavior and
context exports. Native scopes and operation ids distinguish call sites;
private error routing keeps retry identity consistent with the failed action.
No private error fields enter public diagnostics. Recursive function calls,
protocol/catalog shadowing and unbounded expansion fail admission.

Inline Draft 2020-12 references now require a network-free resource closure,
including anchors, dynamic anchors, nested identifiers and escaped/percent
encoded fragments. Data fields in schema const/examples/defaults are not
references. Missing or non-schema targets fail admission; runtime validation
has no network retriever. `referencing==0.37.0` is an explicit pinned dependency;
the standalone core lock also now includes its already-required jq pin.

Deployment-approved Draft 2020-12 schema resources now require exact HTTPS
document URLs and mandatory SHA-256 pins, independent of catalog trust. The
asynchronous compilers freeze a bounded reference closure into workflow identity;
root/reusable/imported input, output and export validate against captured content
without runtime retrieval. Credentials remain deployment-owned and host-bound.
Revoked trust/pins fail before effects. Conflicting identifiers fail admission;
cache revalidation preserves original-byte digest checks. Safe JSON/YAML loading
bounds source and expanded bytes/nodes/depth, including aliases before object
construction. Sandbox admission walks actual task definitions; application data
and schema constants containing `run` retain their literal meaning. See
[schema resource policy](../schema-resources.md).

Asynchronous compilers capture immutable external catalog content/URI/digest
in the plan fingerprint. Application text cannot trigger imports, another
compilation cannot replace captured content, and execution rechecks deployment
endpoint/integrity policy. Protocol templates respect the workflow expression
mode. Imported protocol tasks now use the complete common task pipeline,
including input/output/export, condition/timeout/transition behavior, original
caller pointers and captured task descriptors. Their native scopes also recover
completed calls without replay. Remote definitions use strict safe loading and
official task validation; unsafe code/nested imports/unimplemented protocols
remain explicit rejections.
Local workflow versions use SemVer precedence, reject ambiguous build ties
and cannot overwrite an existing identity with changed content. See the
[qualified resolution contract](../reusable-definitions.md).

Final local root validation passed 1127 tests, 30 skips and 91.66% core coverage.
Independent native/contract/CTK matrices passed ADK 517, LangGraph 527 with
6 unconfigured PostgreSQL skips and optional Agent Framework 683. Imported
protocol tasks share inline validation, input/output/export, condition, timeout
and control-flow semantics. Native SQLite/PostgreSQL recovery fixtures include
an imported task scope and a captured schema resource; legacy registered calls preserve context and end/exit
behavior through the common pipeline. Reusable script/shell fixtures pass
through the existing SandboxManager on both independent principal engines.
The previous revision `0c113c0` passed all remote gates, including
[CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38142313655),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38142313774),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38142313682),
[optional Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38142313598),
[security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38142313636)
and [documentation](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38142313732).
Revision `bc4cb93` passed root, Windows, mutation, native engines, PostgreSQL,
external sandbox, optional Agent Framework, security and docs checks. Packaged
runtime acceptance failed when a prebound ProfileAuthentication's security
configuration was not reused; its named policy now resolves against that
configuration, with missing profiles rejected before requests and credentials
bound to the catalog host. Imported A2A tasks also now share inline OWS method
translation before wire dispatch. The packaged catalog check includes both
legacy registration and frozen schema-resource resolution; its program passed
locally. Exact remote acceptance is green at `f7be5d115e337c551686a8cdebc62284329e9805`.
Both packaged ADK/LangGraph images passed catalog/resource, internal sandbox,
embedded-default, startup and restart/resume acceptance. Ruff, formatting, strict mypy,
five core/runtime locks, documentation links and root/core builds passed.
Authentication, secrets, extensions, full reliability/proposed inventory tests
and independent JS retain later-stage ownership. S1-07 is verified at its
recorded Python resolution/admission scope; full proposed inventory criteria
remain unclaimed. No release is published.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38145342408](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145342408) | success |
| CI (push) | [38145338954](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145338954) | success |
| External Sandbox CI | [38145342355](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145342355) | success |
| PostgreSQL CI | [38145342373](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145342373) | success |
| Agent Framework CI (optional) | [38145342274](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145342274) | success |
| Security | [38145342415](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145342415) | success |
| Docs | [38145342449](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38145342449) | success |


### S1-06 embedded/bootstrap defaults — 2026-10-11 (verified)

Repository Compose now starts only the selected engine with embedded SQLite;
local PostgreSQL moves to an explicit `compose.postgres.yaml` override that
retains its service/volume names and readiness dependency. Existing PostgreSQL
deployments must keep that override; switching files performs no migration.
No configuration/model binding selects the deterministic fake model and the
generated agent workflow. Model-only external bindings still infer LiteLLM,
and explicit provider selections are preserved. Bootstrap validation errors
omit rejected values/YAML source and suppress sensitive exception tracebacks.
[Bootstrap ownership and compatibility](../embedded-bootstrap.md) distinguish
workflow behavior/fingerprints from provider/storage/deployment bindings and
keep proposed inventory settings inactive.

Local root validation: 989 passed, 24 skipped, 91.30% core coverage. Independent
native/contract/CTK matrices: ADK 393; LangGraph 400 with 3 unconfigured
PostgreSQL skips; optional Agent Framework 532. Both Compose configurations
validate locally, and each base engine selection resolves to exactly one
runtime service. Shell syntax, Ruff, formatting, strict mypy, all four locks,
documentation links and root/core builds passed. Shared tests prove
no-configuration invocation/restart and unchanged fingerprints across
bootstrap model bindings. CI now checks both Compose selections and each
runtime image without config/workflow/knowledge mounts or model credentials,
including restart on its embedded data volume. Exact remote acceptance is
green at `c2094a08fcd4eb90c5f9a085a0428157e5f23717`. CI root validation passed 993 tests,
20 skips and 91.33% core coverage; the shared contract suite passed 291 tests.
Both native/CTK, Windows, mutation, manifests, runtime images, PostgreSQL,
external sandbox, optional Agent Framework, security and docs gates passed.
No release is published.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38138927650](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138927650) | success |
| CI (push) | [38138925253](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138925253) | success |
| External Sandbox CI | [38138927659](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138927659) | success |
| PostgreSQL CI | [38138927624](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138927624) | success |
| Agent Framework CI (optional) | [38138927646](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138927646) | success |
| Security | [38138927657](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138927657) | success |
| Docs | [38138927625](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38138927625) | success |


### S1-05 native task execution — 2026-10-11 (verified)

The LangGraph adapter now lowers immutable derived scopes into native
StateGraph task nodes and conditional edges. Common task semantics/services,
identity, errors and sandbox execution are preserved. Native scope checkpoint
keys include invocation/fingerprint/reference/occurrence; snapshots exclude
live controls, services, handles and internal plans. Synchronous native task
checkpoints skip completed tasks on restart. JSON null stays ordinary data.
Capabilities expose native-node availability and the native per-scope guard.
See [compiler/recovery boundaries](../native-task-execution.md).

Local root validation passed 977 tests, 20 skips and 91.23% core coverage.
Focused native topology/checkpoint/restart, common operator and benchmark tests
passed 28 tests. Completed fork branch caches cannot advance a sibling's
occurrence cursor on replay. Native failures are sanitized into common errors.
Ruff, formatting, strict typing, all four locks, documentation links and
root/core package builds passed. Independent native/contract/CTK matrices
passed: ADK 389, LangGraph 395, optional Agent Framework 526; the final
LangGraph backend-selection revision passed 396 tests with 3 unconfigured
PostgreSQL skips. A fresh SDK-only environment without SQLite passed 8 tests
with 10 explicit backend/dependency skips. PostgreSQL CI separately exercises
its saver without installing SQLite. Exact remote
acceptance is green at `6a3afd793c170f38c5547bf9218ce83e4e04e265`. Full crash recovery,
ordered fork reducers, durable winners/waits/events, historical checkpoint
migration and independent JS qualification remain staged work.

Remote root validation: 982 passed, 20 skipped, 91.26% coverage; shared
contracts: 287 passed. Native LangGraph contracts: 300 passed, 3 unconfigured
PostgreSQL skips; adapted CTK: 96 passed. The isolated PostgreSQL native
checkpoint job passed 6 tests with 4 deliberate SQLite-specific skips, proving
its saver operates without SQLite installed. Windows native graph/checkpoint
checks passed.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38137735635](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137735635) | success |
| CI (push) | [38137731911](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137731911) | success |
| External Sandbox CI | [38137735617](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137735617) | success |
| PostgreSQL CI | [38137735715](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137735715) | success |
| Agent Framework CI (optional) | [38137735666](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137735666) | success |
| Security | [38137735602](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137735602) | success |
| Docs | [38137735580](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38137735580) | success |

Both native/CTK matrices, runtime images, external sandbox and PostgreSQL
acceptance passed, with Windows, mutation, manifests, optional Agent Framework,
security and docs. No release or broader conformance claim was published.


### Stage 1 admission work — 2026-10-11 (verified)

S1-01 preserves the official schema bytes and records separate prerelease-tag
and upstream-schema pins. Capabilities expose the bounded profile provenance.
The common loader rejects duplicate keys, cyclic/non-JSON YAML and excessive
nesting. Admission rejects ignored expression configurations, reusable
components, external/non-JSON data schemas, named errors/timeouts, listener
iterators/correlation, inline endpoint authentication and background/return modes. Nested diagnostics preserve
task pointers; inline JSON Schema checks include boolean schemas.
See [the profile and boundaries](../workflow-admission.md).

Local root validation passed with 734 tests, 13 skips and 90.64% coverage.
The independent native ADK and LangGraph matrices passed 249 and 247 tests
before the final two authentication-admission assertions; optional Agent
Framework passed 333. The final focused workflow/protocol/admission suite
passed 90 tests. Ruff,
formatting, strict mypy, all four runtime locks, Markdown links, root/core
builds and packaged schema/profile checks passed. Both native benchmark
probes recorded checkpoint growth and stable operation ids across replay.

Exact code-commit acceptance at `df4736146ad964c1d6d544759a3b934ca086575b`:

| Gate | Run | Result |
| --- | --- | --- |
| CI (PR) | [38131069592](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38131069592) | success: root/coverage, mutation, manifests, both native/contract/CTK matrices and both runtime-image acceptance jobs |
| CI (push) | [38131066073](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38131066073) | success |
| External Sandbox CI | [38131069556](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38131069556) | success: contracts, controller image and both Docker-backend runtime acceptance jobs |
| PostgreSQL CI | [38131069642](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38131069642) | success: common stores and both native-engine persistence jobs |
| Docs | [38131069597](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38131069597) | success |

Validation exposed two portability defects: frozen snapshots needed binary
checkout attributes to preserve their exact upstream hashes on Windows;
ADK's pinned SQLite migration probe retained a synchronous connection until
garbage collection. The adapter closes that probe explicitly and closes its
native event iterator/runner. Regression coverage verifies temporary storage
cleanup after restart and preserves rejection of the old native schema.
Native persistence remains owned by ADK.

Local Docker availability probes timed out; container, Linux mutation and
PostgreSQL acceptance were verified in remote CI. No release, image publication,
schema modification, broader conformance claim or later-stage delivery is
recorded by this work.

### S1-02 expression work — 2026-10-11 (verified)

Core now uses `jq==1.12.0` (bundled jq 1.8.2) in every dependency environment.
Strict/loose recognition, scoped syntax admission, native jq operators and
filters, single-result enforcement, finite JSON/safe-integer handling and
sanitized common errors replace the partial evaluator. Input-derived strings
remain data. Invocation modes are isolated; sandbox argument/stdin/environment
conversion is shared and script code remains literal. Ambient I/O, module and
clock access are blocked, with an admission check for libjq's automatic home
module loading. [Expression limits](../workflow-expressions.md) document the
in-process boundary and the outstanding data-flow/descriptor work.

Local root validation: 849 passed, 13 skipped, 90.54% core coverage before the
last numeric/custom-index edge cases; the final core jq suite passes 102 tests.
Independent native adapter/contract/CTK suites passed: ADK 264, LangGraph 262,
optional Agent Framework 355. The added sandbox jq cases then passed in each
native environment (16 ADK, 16 LangGraph, 24 optional Agent Framework shared
expression tests). Final root validation passed 851 tests, 13 skipped, with
91.04% coverage; Windows expression/admission CI also passed. Concurrent jq
invocations exposed a fresh-SQLite WAL initialization race in native LangGraph.
The native saver now prepares storage before first concurrent writers, without
serializing workflow execution or moving checkpoint ownership into core. A
12-workflow barrier/checkpoint regression and the complete LangGraph matrix
(265 passed) verify the correction. Ruff, formatting, strict mypy, four lock checks, Markdown
links and root/standalone-core builds passed. Exact-commit remote acceptance is green at `3ab2e73d29fcfd94afa1c6efe23bb481d53c41dd`. Adapted CTK
syntax is documented; proposed inventory tests and JS delivery are unclaimed.


| Gate | Run | Result |
| --- | --- | --- |
| CI | [38133078110](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38133078110) | success |
| External Sandbox CI | [38133078098](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38133078098) | success |
| PostgreSQL CI | [38133078062](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38133078062) | success |
| Agent Framework CI (optional) | [38133078074](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38133078074) | success |
| Security | [38133078050](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38133078050) | success |
| Docs | [38133078048](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38133078048) | success |

CI root validation passed 855 tests, 10 skipped, with 91.07% coverage. All native/CTK, Windows, mutation, manifests, runtime-image, external-sandbox and persistence gates passed. This qualifies the jq foundation; S1-03 data-flow/descriptors and all later stages remain open. No release was published.

### S1-03 data-flow work — 2026-10-11 (verified)

Core now validates raw workflow/task input before transformation, executes
task bodies on local transformed data, validates transformed output, and
evaluates exports against that output before replacing context. Context is
any admitted JSON type and is separate from invocation identity/lifecycle
metadata. Nested tasks preserve parent descriptors; sequential loop exports
carry forward; fork inputs/bindings are copied. Skips bypass all data stages
and operation effects. Runtime/workflow/task/time descriptors and expression
phase scope are implemented; authorization/secrets remain rejected pending
their security contracts. [Data-flow semantics](../workflow-data-flow.md)
records the pipeline and the later-stage boundaries.

Independent native adapter/contract/CTK matrices passed: ADK 327,
LangGraph 326 and optional Agent Framework 435. The focused pipeline,
jq and lifecycle suite passed 184 tests; the final pipeline fixture
passes 61 tests after adding descriptor-definition assertions. Ruff,
formatting, strict mypy, all four lock checks, Markdown links and
root/standalone-core builds passed. Windows CI now includes pipeline
fixtures alongside jq/admission checks.

Contract validation caught a retry-event identity regression caused by
task-local descriptors. Retry events now derive the failing child reference
from the common error and preserve its side-effect operation id. The official
schema and frozen source snapshots are unchanged. Exact-commit remote acceptance
is green at `56e9f2c4b14c7cec2ad6ab4f3bb68accb5e2fc6d`. Later transitions, fork aggregation/reducers,
recovery, proposed-test inventory and JS delivery remain unclaimed.

Local root validation passed 914 tests, 14 skips and 91.07% coverage.
Remote root passed 918 tests, 10 skips and 91.10% coverage; its shared
contract run passed 225 tests.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38134434733](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134434733) | success |
| CI (push) | [38134431996](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134431996) | success |
| External Sandbox CI | [38134434744](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134434744) | success |
| PostgreSQL CI | [38134434680](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134434680) | success |
| Agent Framework CI (optional) | [38134434696](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134434696) | success |
| Security | [38134434712](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134434712) | success |
| Docs | [38134434699](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38134434699) | success |

Both native/CTK, runtime-image, external-sandbox and native PostgreSQL gates
passed, along with Windows, mutation, manifests, security and docs. No
release or broader conformance claim was published.

### S1-04 control-flow work — 2026-10-11 (verified)

Common task routing now distinguishes scope exit from workflow end, validates
named transition targets in scope before effects, preserves backward jumps,
defers switch defaults until no predicate matches, rejects multiple defaults,
and respects explicit case/catch directives. Ordered loop bodies honor
`while`, carry data/context and reject non-array collections before effects.
Original JSON pointers escape names and preserve fork branch structure.
Workflow end cancels/awaits active fork siblings while preserving the ending
branch's ancestor scopes. Cancellation events distinguish graceful end from
invocation cancellation. [Control-flow details](../workflow-control-flow.md)
record the contract and the historical operation-id compatibility boundary.

Independent native testing exposed that LangGraph and optional Agent Framework
treat native `None` as a control/no-input signal. The bridges now encode/decode
JSON null with a private non-JSON bytes marker; non-null LangGraph checkpoint
inputs remain unchanged. Shared fixtures verify all JSON types, nested forks,
scope exit/end, original references, early loop stop, routing and cycles.
Later native task lowering, reducers, durable winner/recovery/migration and
independent JS delivery remain unclaimed. Exact-commit acceptance is green
at `0dc871d5a143feb9043d0d4288a8e7d37ee3dad0`.

Local root validation before the final ending-ancestor failure assertions
passed 972 tests, 14 skips and 90.49% coverage. Independent native
adapter/contract/CTK matrices passed ADK 385, LangGraph 384 and optional Agent
Framework 520. The final scoped-flow revision passed 60 fixtures in each ADK
and LangGraph environment, and 88 in the optional Agent Framework environment.
Ruff, formatting, strict mypy, four lock checks, Markdown links and root/core
builds passed. Windows CI includes the new shared flow fixtures.

The final loop-root regression preserves the loop task's transformed input
for `while` while body outputs evolve. Its complete scoped-flow fixture passes
62 tests in each ADK/LangGraph environment and 91 in optional Agent Framework.

Remote root validation passed 980 tests, 10 skips and 91.26% coverage; its
shared contract run passed 287 tests.

| Gate | Run | Result |
| --- | --- | --- |
| CI | [38135683393](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135683393) | success |
| CI (push) | [38135679455](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135679455) | success |
| External Sandbox CI | [38135683420](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135683420) | success |
| PostgreSQL CI | [38135683388](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135683388) | success |
| Agent Framework CI (optional) | [38135683392](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135683392) | success |
| Security | [38135683390](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135683390) | success |
| Docs | [38135683395](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38135683395) | success |

Both native/CTK, runtime-image, external-sandbox and native PostgreSQL gates
passed, with Windows, mutation, manifests, optional Agent Framework, security
and docs. No release or broader conformance claim was published.

The runtime pipeline is:

```text
load
  -> official Open Workflow schema validation
  -> Portable Profile capability gate
  -> normalize
  -> immutable canonical execution plan
  -> selected engine
```

Core is framework-neutral. Engine adapters own framework-native construction, execution, checkpoints, and resume behavior.

Production runtime engines:

```text
ADK
LangGraph
```

Optional evaluation adapter:

```text
Microsoft Agent Framework
```

Every request executes a workflow. If no workflow is supplied, the runtime generates the default one-task workflow.

Common protocol/runtime services remain in core where practical:

```text
HTTP
MCP
A2A
OpenAPI
knowledge
memory
invocation metadata
approvals
scheduling
sandbox policy
lifecycle events
security policy primitives
```

Engine-native state never becomes the public API contract.
