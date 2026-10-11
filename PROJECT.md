# Project Context

## Source of Truth

- `Project Definition.md` — architecture/product contract.
- `PROJECT.md` — verified implementation and release state.
- `TODO.md` — scoped backlog and intentionally deferred work.
- `AGENTS.md` — mandatory repository/contributor rules.

## Current Phase — 2026-09-24

`v0.1.0` is the previous formal release. `v0.2.0` is the current formal
release for the principal-bound A2A task-access work. It was published from
verified commit `5d1bce6309654c3ada632483fbae71d0404cc33d`. The readiness
foundation is complete; stronger Agent Framework support and external event
integration are optional future tracks, not blockers for the current
open-source core.

The bounded inbound A2A profile is complete end to end: common Task projection with get/cancel, deployment-declared skills, per-principal authorization (`a2a.authorization`), principal-bound authenticated task access, waiting→`input-required` mapping, protocol-native `returnImmediately` async behavior, resuming sends over the common resume contract, and bounded streaming/resubscription (`SendStreamingMessage`/`SubscribeToTask`) translating common lifecycle events into official status/artifact frames. Shared security profiles are wired across all inbound/outbound adapters and every temporary credential field is removed. The deployment-controlled `traffic_policy` model is implemented with global token-bucket/concurrency limits plus endpoint-prefix and authenticated-principal scopes. External interoperability/conformance evidence is complete for all advertised baselines.

No broad A2A, MCP, OpenAPI, CloudEvents, Open Workflow, OpenShift, or multi-engine conformance claim is made beyond the exact tested capability/profile boundaries.

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

### S2-03 checkpoint recovery — 2026-10-11 (in progress)

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
See [recovery boundaries](docs/checkpoint-recovery.md).

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
configured gate; exact new-source GitHub/database acceptance remains pending.
Conditional metadata writes are not an exclusive execution lease or an atomic
fence around native checkpoint writes.

S2-03 stays open. This fix does not reconcile all native/common invocation or
side-effect records. General pending-effect reconciliation, fenced recovery ownership,
missing or inconsistent native checkpoint recovery and broader configured
database acceptance still require work. Legacy rows without an original-input record
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
built-in error-status catch fallback gap remains tracked for S2-04.

### S2-01 ordered fork results — 2026-10-11 (verified)

Noncompetitive fork returns the declaration-ordered array of all branch
outputs, retaining overlapping keys and JSON null/scalar/list/object values.
Original input is no longer implicitly merged. Existing assertions that
encoded merged output now follow the pinned OWS rule. Common output/export
can explicitly aggregate results or merge objects through jq; branch-local
exports remain isolated from siblings and parent context. See
[fork behavior and migration](docs/workflow-forks.md).

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

The [implementation audit](docs/current-implementation-audit.md) covers all
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
[schema resource policy](docs/schema-resources.md).

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
[qualified resolution contract](docs/reusable-definitions.md).

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
[Bootstrap ownership and compatibility](docs/embedded-bootstrap.md) distinguish
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
See [compiler/recovery boundaries](docs/native-task-execution.md).

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
See [the profile and boundaries](docs/workflow-admission.md).

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
module loading. [Expression limits](docs/workflow-expressions.md) document the
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
their security contracts. [Data-flow semantics](docs/workflow-data-flow.md)
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
invocation cancellation. [Control-flow details](docs/workflow-control-flow.md)
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

## Formal Release v0.1.0 — 2026-08-28

Release commit:

```text
c47cb86
```

Release workflow:

```text
33136714445
```

Companion acceptance for the release head:

| Workflow | Run | Result |
| --- | --- | --- |
| CI | `33136592588` | green |
| Security | `33136597832` | green |
| External Sandbox CI | `33136592592` | green |
| PostgreSQL CI | `33136592632` | green |
| Release | `33136714445` | success |

Published runtime image digests for `0.1.0`:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk
sha256:4d89ffaa88207488fec4b128e1e728282cca701f0e31330c7314c1606235cf36

ghcr.io/bassemzohdy/open-workflow-agent-langgraph
sha256:add38f52c062a01ab81c61962ab609a728e62a367818cc77bff19a6a720d2a89

docker.io/bzohdy/open-workflow-agent-adk
sha256:4d89ffaa88207488fec4b128e1e728282cca701f0e31330c7314c1606235cf36

docker.io/bzohdy/open-workflow-agent-langgraph
sha256:add38f52c062a01ab81c61962ab609a728e62a367818cc77bff19a6a720d2a89
```

Standard ADK/LangGraph images remain roughly 266 MB / 248 MB decimal after the 2026-08-27 dependency refresh and avoid the multi-gigabyte Torch/CUDA path.

## Formal Release v0.2.0 — 2026-09-12

Release commit:

```text
5d1bce6309654c3ada632483fbae71d0404cc33d
```

Release tag: `v0.2.0`

GitHub Release workflow: `34701171499`

GitHub Release: https://github.com/bassemZohdy/open-workflow-agent/releases/tag/v0.2.0

The release contains the principal-bound A2A task-access contract, synchronized
runtime/package metadata, and the sandbox-controller cleanup fix required by
external acceptance.

### Release evidence

| Workflow | Run | Result |
| --- | ---: | --- |
| CI (main) | `34701012803` | green |
| CI (tag) | `34701014864` | green |
| Security (tag) | `34701014868` | green |
| External Sandbox CI | `34701012783` | green |
| PostgreSQL CI | `34700667802` | green; ancestor `5a41467`, unchanged path |
| Agent Framework CI | `34700667743` | green; ancestor `5a41467`, unchanged path |
| Documentation | `34700667852` | green; ancestor `5a41467`, docs unchanged in final fix |
| Release | `34701171499` | success |

The release workflow passed dependency/image scanning, OCI SBOM generation,
OCI provenance generation, and GitHub build-provenance attestation for the
published images. Companion runs shown as ancestor runs covered paths that
were unchanged by the final sandbox cleanup fix; the exact release commit was
covered by the main/tag CI, security, external-sandbox, and release runs.

### Published image references

The following immutable GHCR references are the canonical release references.
The same release build and version/SHA tags were published to Docker Hub.

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk@sha256:156bc95fb6df508d5c446317ff06d54f4e7dcb25adfa33bc5939cb06dbdfb714
ghcr.io/bassemzohdy/open-workflow-agent-langgraph@sha256:9691f672b2b169ffb1b7b127eea7267846f20df707be4800d5e707cd5699791d
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller@sha256:45cc85803be5094200709b6a2ed05af4fc4034b5bf774a5d447146f5d1892001
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller@sha256:9074ec5348bf4961a78c804fb70cc6b92facc89ec3c1422f30ea526ee3a16859
```

Rollback references are the previous formal release images recorded above
under `Formal Release v0.1.0`; rollback means selecting those immutable image
digests and reverting the deployment configuration to the prior contract.

## Verified main maintenance publication — 2026-09-24

Commit `95aeb02f991d35bc265467e0e4a03f05e732d9d0` contains the grouped
dependency updates and Debian runtime-image security fixes. This is a rolling
`main` image publication, not a new formal release; `v0.2.0` and its tagged
image digests above remain the current formal release.

| Workflow | Run | Result |
| --- | ---: | --- |
| CI | `35933526556` | green |
| PostgreSQL CI | `35933526627` | green |
| External Sandbox CI | `35933526511` | green |
| Release | `35933779422` | success; all four image jobs passed |

Canonical GHCR image references resolved from the published
`sha-95aeb02f991d` tags:

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk@sha256:746835c1abd2fb81065ffa9fd3e3106d7dd3d3aa5e4700403a3bca283e50dbfe
ghcr.io/bassemzohdy/open-workflow-agent-langgraph@sha256:e65dacf2bfb6db12afff4de17c6fcc473b779ecf79d5e627dd40cf3d81738f20
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller@sha256:7686bcddb2e98d1f54fae408820982b698f0421c44ed44044d4582d4b2bbd5d0
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller@sha256:6666e22260845bdbacbfb2c1de8913864e8f4d9c628a95d760a4ef46e9de7e5a
```

The preceding `main` release workflow at `64dc212` failed image
vulnerability scans for the ADK, LangGraph, and Kubernetes controller images
while the Docker controller published successfully. The passing maintenance
run fixed those 13 common Debian OS findings. The publication sequencing
issue was resolved by the verified release integrity maintenance below.

## Verified release integrity maintenance — 2026-09-24

PR [#48](https://github.com/bassemZohdy/open-workflow-agent/pull/48) merged as
`8369b025657614e71cc7cb4baf2e508f3d8271b3`. This is a rolling `main`
publication, not a new formal release. `v0.2.0` remains the current formal
release.

| Workflow | Run | Result |
| --- | ---: | --- |
| CI | `35936514346` | green |
| PostgreSQL CI | `35936514236` | green at the exact commit |
| External Sandbox CI | `35936514292` | green at the exact commit |
| Release | `35936756631` | success; all eight image/platform preflight scans and all four publish jobs passed |

The Release prepare job accepted only those exact-commit companion runs. The
workflow serialized publication across source commits and verified the current
`main` head before and after the acceptance wait. It ran High/Critical OS and
library scans for all four images on both `linux/amd64` and `linux/arm64`
before any registry push. Runtime and controller publish jobs completed with
provenance attestations. The formal GitHub Release job now requires both
runtime and controller publication.

The following canonical GHCR digest references were resolved from
`sha-8369b0256576`. The same four index digests and both platform manifests
were independently verified at Docker Hub under `docker.io/bzohdy/`.

```text
ghcr.io/bassemzohdy/open-workflow-agent-adk@sha256:32bc89bfd913e1d58c6f94bcb27fa8017e38ab28ae45f9b91fe9e43774441231
ghcr.io/bassemzohdy/open-workflow-agent-langgraph@sha256:2f3c3300619fe8fea24f4bc85a0f0049b88b00be2beccbcd5270d16e1d38fe39
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller@sha256:a01ee8d170ad9a122d9523e9c61453972fdee668641a33a2bfe7e91e90dc883a
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller@sha256:46261f984bfd1652686202b55a55a5cb7f857b20060fed5f02989136ec23521e
```

The live release run proves the success path and the publication order. The
deterministic tests cover failed, missing, delayed, and ancestor-only
companion runs plus the scan dependency graph. A registry failure after
preflight can still leave some rolling tags updated. Digests remain the stable
deployment reference.

## Verified Follow-up Work — 2026-09-12 (release)

- `A2A-8` through `A2A-10`: authenticated A2A task ownership is persisted in
  common invocation metadata, enforced for get/cancel/subscribe/resume across
  JSON-RPC and HTTP+JSON, and covered by deterministic cross-principal,
  unknown-task, transport, and sanitized-error tests.
- `RELEASE-2`: exact-commit release readiness was completed for
  `5d1bce6309654c3ada632483fbae71d0404cc33d`; metadata, locks, source state,
  quality gates, engine evaluations, runtime/container acceptance, external
  sandbox, PostgreSQL, security, documentation, image, SBOM, and provenance
  evidence are recorded above.
- `RELEASE-3`: `v0.2.0` was merged to `main`, tagged, published as a GitHub
  Release, and pushed with immutable runtime/controller image references.
- `ENGINE-4`: retain the current native execution-envelope architecture for
  this release. Keep capability and documentation language narrow; do not
  introduce a task-level native compiler unless a separate product requirement
  justifies it. If that requirement appears, begin with LangGraph’s stronger
  native resume path and prove ADK parity without changing the public DSL or
  exposing the internal plan.

## Verified Kubernetes and OpenShift Sandbox Acceptance — 2026-09-12

Kubernetes real-cluster acceptance is green on kind with Kubernetes 1.37 and Calico NetworkPolicy enforcement.

Verified behavior includes end-to-end `run.container`, numeric non-root execution, timeout/cancellation cleanup, secret-safe injection, namespace-bounded controller RBAC, and default-deny workload networking.

OpenShift-specific acceptance is green on a disposable OpenShift 4.22.1 cluster provided by Red Hat OpenShift Local (CRC). The `restricted-v2` SCC injected UID `1000720000` for both the controller and sandbox workload, and the acceptance harness verified arbitrary-UID execution, restricted security contexts, namespace-scoped RBAC, default-deny cluster egress, writable `/workspace`, read-only roots, and cleanup.

The run used the published immutable images:

```text
ghcr.io/bassemzohdy/open-workflow-agent-kubernetes-sandbox-controller@sha256:428ac2d1e5aa71494100a9f41f4c22defcc47a6d1f63c0cfe1178d0776a5f1c9
ghcr.io/bassemzohdy/open-workflow-agent-sandbox-controller@sha256:cae2d897a07fd038bbfe8bd6b00a668d64b06ad34b887769b79fe881d8f1098e
```

## Bounded Lifecycle Streaming

Common lifecycle SSE is implemented at:

```text
GET /v1/events/lifecycle/stream
```

It is a bounded engine-neutral observation stream over common lifecycle CloudEvents. It is not token/output streaming and is not itself an A2A binding.

Implemented guarantees include bounded replay with `Last-Event-ID`, bounded queues/subscriber count, byte/event/lifetime limits, sanitized lifecycle payloads, and no engine-native checkpoint/stream exposure.

## Protocol Baselines

Pinned reviewed baselines are machine-readable in `resources/protocol-baselines.yaml`:

| Protocol/specification | Baseline | Current position |
| --- | --- | --- |
| Open Workflow Specification | `1.0.3` | implemented Portable Profile subset |
| A2A Protocol | release `1.0.1`, protocol `1.0` | bounded v1 SendMessage + Task get/cancel profile |
| Model Context Protocol | `2026-07-28` | bounded common client/tool profile |
| OpenAPI Specification | `3.2.0` | bounded operation adapter; no full parser/conformance claim |
| CloudEvents | `1.0.2` | bounded lifecycle events using `specversion: 1.0` |
| AsyncAPI | `3.1.0` | future binding baseline; readiness plan exists; not implemented |

A deterministic root test guards baseline drift across the manifest, runtime constants, supported bounded method sets, and documentation. Protocol versions never float automatically at runtime.

### Open Workflow vs A2A method vocabulary

Open Workflow 1.0.3 defines its own A2A call method vocabulary such as `message/send`, `tasks/get`, and `tasks/cancel`. OWA preserves the official Open Workflow schema unchanged and translates those schema-defined call values inside `RuntimeServices.call_protocol()` to the official A2A v1 wire operations (`SendMessage`, `GetTask`, `CancelTask`, etc.).

Therefore:

- workflow DSL method names are not external A2A compatibility aliases;
- the external A2A JSON-RPC endpoint accepts the official v1 operation names only;
- A2A v0.3 wire aliases remain intentionally unsupported.

## Current A2A State

Source of truth for wire behavior: the official A2A Project specification/definitions at `a2a-protocol.org`.

Pinned baseline:

```text
A2A release:   1.0.1
protocol:      1.0
```

The bounded inbound A2A profile is implemented, disabled by default, and deployment-controlled.

Implemented endpoints/operations:

```text
GET  /.well-known/agent-card.json

JSON-RPC at <configured A2A path>
  SendMessage
  GetTask
  CancelTask
  SendStreamingMessage
  SubscribeToTask

HTTP+JSON
  POST <configured A2A path>/message:send
  GET  <configured A2A path>/tasks/{id}
  POST <configured A2A path>/tasks/{id}:cancel
  POST <configured A2A path>/message:stream
  POST <configured A2A path>/tasks/{id}:subscribe
```

Implemented Task model:

```text
A2A Task id        = OWA invocation_id
A2A contextId      = OWA session_id
Task state         = projection of common invocation status
Task artifacts     = sanitized common invocation output
owner_principal    = authenticated A2A creator identity (not wire-visible)
engine references  = never exposed
```

Current state mapping:

| OWA invocation | A2A Task state |
| --- | --- |
| `running` | `TASK_STATE_WORKING` |
| `waiting` | `TASK_STATE_INPUT_REQUIRED` |
| `completed` | `TASK_STATE_COMPLETED` |
| `faulted` | `TASK_STATE_FAILED` |
| `cancelled` | `TASK_STATE_CANCELED` |

Task output projection follows official v1 Part JSON shapes: text uses `text`, structured JSON uses `data`, and byte output uses base64 `raw`. Waiting/failure status messages include the Task/context identifiers and only sanitized common error information.

Official bounded Task error mappings implemented for JSON-RPC:

```text
Task not found       -32001
Task not cancelable  -32002
```

HTTP+JSON uses the matching official-style 404/400 boundary for the same conditions.

`SendMessage` follows official async semantics: blocking sends return `result.message` on completion or `result.task` when the workflow ends up waiting (`TASK_STATE_INPUT_REQUIRED`); `configuration.returnImmediately: true` starts the invocation and returns the Task projection immediately for `GetTask` polling; sends carrying `message.taskId` resume a waiting task through the common resume contract (fingerprint-verified), while unknown or non-waiting tasks are rejected with sanitized errors.

When A2A authentication is configured, each A2A-created invocation persists the
authenticated principal identity in common invocation metadata. Get, cancel,
subscribe, and resume require that same identity. Unknown and cross-principal
references use the same sanitized `task_not_found` response. Existing or
internal handles without an owner are available only in the trusted,
unauthenticated single-principal mode; owner identities are never included in
Task projections, lifecycle payloads, or engine-native state.

Streaming/resubscription (`SendStreamingMessage` over `message:stream`, `SubscribeToTask` over `tasks/{id}:subscribe`) streams official `Task`/`statusUpdate`/`artifactUpdate` frames translated from common lifecycle CloudEvents. Streams are bounded by event/byte/duration limits with fail-closed backpressure, disconnecting never cancels the invocation, and engine-native checkpoint/stream objects are never exposed.

`features.a2a` currently advertises Tasks with exactly `GetTask` and `CancelTask`, `SendStreamingMessage`/`SubscribeToTask` streaming operations, the deployment-declared skill ids, bearer authentication, and whether per-principal authorization enforcement is active; push notifications remain false.

Agent Card now advertises `securitySchemes` and `security` requirements when authentication is configured (bearer scheme), conforming to A2A v1 Agent Card specification patterns.

## A2A Deferred Scope

The bounded inbound protocol profile — Task state, authorization, skills, async semantics, and streaming/resubscription — is complete (see Security Architecture State and Current A2A State). Remaining deferred work is listed in `TODO.md`.

The official A2A v1 semantics are the guide: ordinary `SendMessage` blocks by default, while `returnImmediately=true` is the protocol-native non-blocking request and returns Task state for later `GetTask`/subscription. OWA will not add a custom async flag.

Push notifications remain separately deferred because they create an outbound callback trust boundary requiring allowlisting, TLS identity verification, callback authentication, SSRF protection, replay/idempotency controls, bounded retries/dead-letter handling, and secret-safe observability.

Delegated-user identity, token exchange, and consent remain deployment/identity-platform concerns and do not block Task support.

Interoperability evidence is now provided through capability-accuracy tests, A2A v1 spec conformance tests, security scheme advertisement, and multi-operation consistency tests.

## Security Architecture State

Framework-neutral security primitives are now implemented and deterministically tested for:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Implemented security groundwork:

- named profile model with discriminated profile types;
- secrets represented as deployment environment references, not inline values;
- secret resolution only at use time;
- validation errors hide rejected input values to prevent accidental secret echo;
- OAuth2 client-credential token endpoint requires HTTPS;
- principal/identity, roles, scopes, actions, resources, and audience remain distinct;
- deterministic authorization policy evaluation exists in core;
- `RuntimeConfig.security.profiles` is a strict-parsed section of the main runtime configuration, with `OWA__SECURITY__...` overrides;
- A2A inbound bearer authentication resolves a named `bearer` security profile (`a2a.security_profile`), replacing the temporary `auth_token` field; `RuntimeConfig` rejects unknown or non-bearer profile references at startup; a missing deployment secret at request time fails closed (401), not a crash;
- the approvals operator check (`approvals.operator_security_profile`), external-catalog authentication (`authentication.security_profile`), per-tool authentication (`tools[].security_profile`), and workflow-initiated outbound protocol calls (`protocols.security_profile`) all resolve named `bearer`/`api_key` profiles fail-closed at call time;
- A2A per-principal authorization (`a2a.authorization`) enforces explicit allow rules — `message.send` on `skill:<id>` (or `skill:workflow` for the implicit skill), `tasks.get`/`tasks.cancel` on the `tasks` collection — against the authenticated profile principal; first matching rule allows, no match returns a sanitized 403, and a policy declared without a security profile is rejected at startup. Object-level task access then requires the persisted `owner_principal`; cross-principal and ownerless references on authenticated endpoints return the same sanitized task-not-found response;
- every temporary credential field is removed (`a2a.auth_token`, `approvals.operator_token`, external-catalog `bearer_token_env`/`basic_*_env`, ambient `OWA_BEARER_TOKEN_ENV`/`OWA_BASIC_*` protocol-client variables); credentials resolve exclusively through named profiles;
- secret-safety verification tests assert the resolved token value never appears on Agent Cards, capability documents, A2A Task projections, protocol error bodies, or configuration validation errors.

Still active:

- broader identity-platform integration remains outside OWA; OAuth2 client-credentials and mTLS profile types are wired to the outbound protocol adapter and covered by core tests.

OWA does not become an identity provider. OAuth2/OIDC federation, delegated-user token exchange, and consent remain external identity-platform responsibilities.

Traffic/rate/concurrency policy remains a separate deployment concern from authentication/authorization.

## Traffic Policy State

The deployment-controlled `traffic_policy` model is implemented:

```text
TrafficPolicyConfig
  enabled: bool = false
  rate_limit: RateLimitConfig (token bucket, requests_per_second + burst)
  concurrency_limit: ConcurrencyLimitConfig (max_concurrent)
  endpoint_limits: list[TrafficEndpointLimitConfig] (longest path-prefix scope)
  principal_limits: list[TrafficPrincipalLimitConfig] (authenticated identity scopes)
```

Implemented behavior:

- token bucket rate limiting with configurable requests-per-second and burst capacity;
- concurrent request limiting with configurable max concurrent requests;
- additional longest-matching endpoint-prefix limits and authenticated-principal limits;
- static bearer/API-key profile identities, including the bounded A2A profile, are attached to the request scope before traffic admission;
- 429 responses with structured error codes (`rate_limit_exceeded`, `concurrency_limit_exceeded`);
- ASGI middleware applied only when `traffic_policy.enabled=true`;
- capability advertisement at `/v1/capabilities` under `features.trafficPolicy`;
- full configuration via YAML or `OWA__TRAFFIC_POLICY__*` environment overrides.

Traffic policy is intentionally separate from security profiles — authentication/authorization profiles must not own traffic management.

## Persistence and State Boundaries

The runtime preserves distinct lifecycles for knowledge, memory, session, common invocation metadata, approvals, schedules, sandbox executions, and engine-native checkpoints/state.

SQLite remains the reference datasource. PostgreSQL common stores and ADK/LangGraph native PostgreSQL adapters are implemented with isolated namespaces. Engine-native checkpoint state is never exposed as a public resume or A2A Task contract.

## Verified Follow-up Work — 2026-09-11

The following backlog items are implemented and verified in the current worktree:

- `SECURITY-8`: security response headers are configurable under `server.security_headers`; HSTS is emitted only for HTTPS ASGI requests.
- `DEPS-1`: `numpy` and `pypdf` are optional knowledge dependencies in both root and standalone-core package metadata, with deferred imports.
- `FIX-1`: the default FastEmbed provider is lazy with respect to optional NumPy, so minimal/core-only and isolated engine environments can construct `RuntimeServices`; knowledge operations fail with a bounded error when the extra is absent.
- `FIX-2`: knowledge manifest reuse checks include file hash, parser identity, chunking identity, and embedding identity, so parser/chunking changes trigger deterministic re-indexing.
- `SEC-1` and `SEC-2`: deployment-owned authentication and operation/idempotency headers override workflow/tool payload values; protocol endpoints reject embedded credentials; A2A public-base URLs reject malformed ports, credentials, queries, and fragments; protocol allowlist host matching is normalized.
- `VALIDATE-1`: non-positive protocol timeouts are rejected before request dispatch.
- `DEPS-4`: built-in `agent:1.0.0` and `llm:1.0.0` catalog manifests declare valid input/output JSON Schemas.
- `DOCS-6` and `DOCS-8`: contributor architecture and bounded A2A streaming documentation are current.
- `K8S-3`: runtime-namespace default-deny NetworkPolicy is provided and covered by manifest tests.
- `DEPLOY-2`: the protected release workflow publishes runtime and sandbox-controller images for `linux/amd64` and `linux/arm64`, with a workflow regression test.
- `DEPLOY-1`: the Kubernetes/OpenShift controller leaves the workload UID unset for `platform=openshift` so restricted SCC can inject the project UID range, while retaining the fixed non-root UID for vanilla Kubernetes. The disposable OpenShift 4.22.1 acceptance harness passed with SCC assignment, arbitrary-UID execution, security context, RBAC, network denial, workspace writes, and cleanup verified.
- `OBS-1`: `/metrics` exposes bounded Prometheus text metrics for workflow lifecycle, task and sandbox events, HTTP/A2A traffic, traffic policy, scheduler jobs, and pending approvals.
- `DEPS-3`: strict mypy checks cover core, ADK, LangGraph, and optional Agent Framework adapter packages, with native SDK boundaries explicitly isolated.
- `DOCS-1` through `DOCS-4`: the API guide now documents memory tools, scheduling, approvals, generic events, lifecycle snapshots, and bounded SSE replay.
- `DOCS-5`: the default-profile OpenAPI schema is versioned at `docs/openapi.json` and compared against the generated FastAPI schema in CI tests.
- `DOCS-7`: custom catalog function authoring, manifest layout, protocol boundaries, trust policy, and verification are documented in `docs/custom-catalog-functions.md`.
- `DOCS-9`: deployment configuration recipes cover A2A, security profiles, traffic policy, sandbox backends, protocol tools, and PostgreSQL persistence.
- `DOCS-1` follow-up: the architecture and engine README wording now describes the internal sandbox as a controlled child-process boundary and the production adapters as native execution envelopes; task-level native compilation is not claimed.
- `CI-1a`: documentation-only changes have a lightweight Markdown relative-link workflow, while the full code/test workflow remains path-scoped.
- `RELEASE-1` progress: the restricted Docker sandbox controller removes its unused Docker Compose and Buildx CLI plugins before image scanning; a focused regression test guards the release-image hardening.
- `TEST-2`: dependency-free benchmark harness reports compilation latency, sequential invocation latency, and concurrent throughput as JSON.
- `TEST-3`: core coverage is enforced at 90%; the full locked root suite currently reports 652 passed, 11 skipped, and 90.41% exact coverage, with expanded deterministic tests across protocol, catalog, storage, knowledge, lifecycle, sandbox, API-boundary, scheduling, tool, and server paths.
- `OPS-1`: knowledge watch failures retain safe bounded status, keep the watcher alive for retry, emit safe warning metadata, and surface degraded readiness.
- `OPS-2`: `docs/release-readiness.md` defines exact-commit, dependency, runtime acceptance, supply-chain, publication, and exception sign-off gates.
- `TEST-4`: Linux/WSL-compatible mutmut coverage targets the framework-neutral traffic-policy middleware with 13 direct tests; the current 316-mutant baseline kills 272 mutants, records 22 survivors, 18 timeouts, and 4 mutants without test association for future test-strengthening work.
- `TEST-5`: deterministic 100-request async stress coverage verifies traffic-policy concurrency bounds and burst admission without external services.
- `TEST-1`: the portable CTK subset covers 26 feature files, 48 scenarios, and 96 deterministic executions across both available engines, including pinned upstream data-flow filtering, HTTP and OpenAPI content/response output projections, caught and uncaught protocol errors, alongside successful and failing HTTP, MCP, A2A, OpenAPI, event, catalog-call, registered child-workflow `run`, input rejection, conditional, `for` output, task-schema, `listen` read-mode, flow, policy, transform, validation, nested-input, sequence, and retry scenarios over shared common services; broader upstream CTK coverage and full conformance remain explicitly deferred.
- `K8S-2`: optional Kubernetes Ingress and Gateway API `HTTPRoute` templates target the reference runtime Service; deployment-owned host, TLS, ingress-class, and Gateway-parent values remain explicit placeholders.
- `K8S-4`: optional Prometheus Operator `ServiceMonitor` and `PrometheusRule` templates scrape `/metrics` and alert on HTTP error rate, p95 latency, and sandbox failures; CRD fields are covered by targeted manifest tests.
- `K8S-1`: reusable Helm chart under `deploy/helm/open-workflow-agent` packages the runtime Deployment, Service, PVC, default-deny network policy, and opt-in edge/monitoring integrations; `helm lint` and rendered integration tests pass.
- `ARCH-3`: traffic policy supports additional longest-matching endpoint-prefix and authenticated-principal rate/concurrency scopes, with static HTTP/A2A security profile identities propagated before admission and capability/configuration/enforcement tests.

## Verified Follow-up Work — 2026-09-12

- `TEST-1`: the portable CTK subset expanded to 26 feature files and 48
  scenarios, with `uv run pytest tests/ctk -q` passing 96 deterministic
  ADK/LangGraph executions. The new repository-owned fixtures cover
  conditional branches, `for` output accumulation, task schemas, and `listen`
  read modes; full Open Workflow conformance remains explicitly deferred.
- `ENGINE-2`: `benchmarks/engines.py` records engine compile latency,
  sequential latency, concurrent throughput, native checkpoint growth, and
  interruption/resume side-effect replay as JSON. The locked engine CI matrix
  runs the probe and uploads its evidence separately from the common-runtime
  benchmark. Native ADK and LangGraph probes pass locally.
- `ENGINE-3`: ADK resume behavior is explicitly documented and named
  `replay_with_stable_operation_ids`. Interruption/resume tests and the engine
  probe verify that replayed side effects reuse the common operation id; callers
  remain responsible for durable idempotency.
- Lifecycle cancellation now drains child waits and operation tasks before
  returning, preventing leaked post-cancellation work from touching closed
  persistence resources.

The relevant core tests, package builds, lock checks, repository-wide Ruff,
formatting, and mypy checks passed. The full locked root suite passes with 652
passed, 11 skipped, and 90.41% exact coverage. The locked ADK and LangGraph
matrices each pass with 180 tests; the optional Agent Framework matrix passes
with 258 tests. Native ADK and LangGraph benchmark probes both pass and report
non-zero native checkpoint growth plus stable operation ids across replay. The
documentation relative-link validator also passes.

## Readiness Foundations — 2026-09-13

Two documentation-only readiness plans apply safe practices without changing
the current public capability claims:

- The [Agent Framework production-readiness plan](docs/agent-framework-production-readiness.md)
  defines package isolation, common-plan boundaries, checkpoint security,
  stable executor identity, native resume evidence, acceptance, and release
  gates. Agent Framework remains an optional community adapter; no production
  image or support commitment is required for the current open-source core.
- The [event integration readiness plan](docs/event-integration-readiness.md)
  defines the transport-neutral CloudEvents baseline, event identity and
  schema rules, idempotency, replay, backpressure, security, and the
  requirements for selecting a concrete transport before any future AsyncAPI
  adoption. The current process-local event baseline is sufficient for the
  current core scope.

No runtime, public DSL, capability, or persistence contract changed as part of
these readiness foundations.

## Current Backlog State

The authoritative backlog is `TODO.md`. The earlier core/readiness foundation
remains complete at its recorded scope. The accepted 2026-10-11 consolidation
roadmap adds staged work and full tracking; runtime claims expand only after
verification. `ENGINE-5` through `ENGINE-8`, `EVENT-1` through `EVENT-5`, push
notifications, multi-tenancy and delegated identity stay visible conditional work.

## Canonical repository consolidation — 2026-10-11

This repository is the selected implementation home. All 284 tracked files of
the public standalone ADK predecessor are preserved as frozen Apache references;
selected fixtures are adapted for canonical contract verification. Independent
assistant/pipeline recipes and an eight-family catalog preserve useful patterns
without copying unlicensed source. All 109 Apache files of `specs-agent-platform`
are now imported as references after the user authorized the explained
private-to-public transfer. The origin remains private. GitHub metadata now
confirms all three named predecessors are archived; MIG-02 source-preservation
checks pass. The full tracker records 658 features, 443
proposed tests and 774 schema constraints. This is source consolidation and gap
tracking, not completed native lowering or full OWS/LangGraph conformance.

Local consolidation validation passed: 679 root tests passed, 8 skipped, with
90.47% core coverage; the 16 added fixture/tracker checks passed. Repository Ruff,
formatting, strict mypy and Markdown links passed. These selected fixture runs
use canonical ADK/LangGraph adapters in the root environment, not proof of new
native compiler behavior or JS parity. Remote CI remains a publication gate.

## Intentionally Deferred


- A2A push notifications.
- Broad/full A2A conformance claim until async/streaming/interoperability gates are green.
- Multi-tenancy.
- Delegated-user identity/token exchange/consent inside OWA.

## Verification Rules

A capability is considered shipped only when applicable deterministic tests and required acceptance gates are green.

Core must remain framework-neutral. Shared contract tests are the portability proof.

Protocol baseline changes are compatibility/security changes and must not be treated as ordinary dependency bumps.

## Key Commands

```text
uv sync --locked --extra knowledge
uv run ruff format --check core engines tests
uv run ruff check .
uv run mypy --package open_workflow_agent --package open_workflow_agent_adk --package open_workflow_agent_langgraph --package open_workflow_agent_agent_framework
uv run --locked pytest -q --cov=core/src/open_workflow_agent --cov-fail-under=90
uv build
uv build --directory core
uv run --directory engines/adk --locked --extra native --extra knowledge --with pytest --with pytest-asyncio pytest ../../tests/adk ../../tests/contract ../../tests/ctk -q
uv run --directory engines/langgraph --locked --extra sqlite --extra knowledge --with pytest --with pytest-asyncio pytest ../../tests/langgraph ../../tests/contract ../../tests/ctk -q
uv run --directory engines/agent-framework --locked --extra native --extra knowledge --with pytest --with pytest-asyncio pytest ../../tests/agent_framework ../../tests/contract ../../tests/ctk -q
```

The CI source-integrity gate caught three ignored predecessor reference files missing from the initial PR. Those exact source bytes are now explicitly tracked; the gate remains enforced. MIG-01 transfer/license disposition is complete: both imported sources retain Apache attribution, and unlicensed generic source stays at origin. No active compiler, checkpoint service or unsafe approval implementation is introduced by the frozen reference import.
