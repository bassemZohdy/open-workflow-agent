# S2-07 child-workflow acceptance

Status: finite CH01-CH10 Python profile verified at
`6b23e5dfcae8bd22b7f5ec7f60c5bbb1bfa8259c`. S2-07 is complete after
the required exact-source native, PostgreSQL, Windows, image and security gates.
The current integrated batch adds parent-owned immutable await/wake decisions,
private ADK/LangGraph suspension and verified fork reentry, original deadline
delivery, synchronous cancellation cascades and bounded crash reconciliation.
The common store holds identities and policy receipts; native checkpoints
remain private. New shared fixtures cover actual process-stop boundaries,
terminal/null/fault results, nested and mutual recursion, corruption, deadlines
and fork cancellation. S2-06's finite profile is verified separately.
General lifecycle/uncertain external actions and version migration retain
S2-08/S2-09 ownership; full proposed criteria and JavaScript remain Stage 4.

## Final hosted qualification — 2026-10-13

All six configured workflows pass for exact feature source `6b23e5d`:
[CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38343010482),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38343010513),
[Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38343010496),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38343010486),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38343010508)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38343010501).

Root passes 2,325 cases and its unchanged 90% coverage gate. Main native suites
pass 1,204 ADK and 1,243 LangGraph cases, with another 96 CTK checks each.
Optional Framework passes 1,639. Declared unavailable-profile skips and the two
tracked protocol expected failures remain explicit. PostgreSQL passes 439
common-store checks (one skip), 387 LangGraph, 353 ADK and 183 optional Framework
checks (seven skips), plus ten native saver checks (eight skips). All 44 new
child cases and 42 scheduling cases pass per principal. The Windows timing job
passes all 337 selected available cases after command correction; its other
three platform profiles also pass. Both main and both PostgreSQL images pass.

All seven complete dependency reports contain zero advisories. Both benchmarks
prove actual native checkpoint growth and one immutable interruption/resume
action. Each SDK's CTK artifact matches all 26 scenario hashes from the exact
Git blobs; the recorded PR merge has this feature head as a parent. Local CRLF
checkout bytes are not substituted for canonical source bytes. The earlier
inspector mismatch is retained separately, as are failed/cancelled source
`026969c` checks. Full logs, artifacts and the finite inspection are retained
under `tmp/ci-6b23e5d-*`; the tracker stores the complete qualification record.

## Integrated local validation — 2026-10-13

Hosted source `026969c` passes Docs, optional Framework, external sandbox and
all seven security audits, but does not qualify S2-07. PostgreSQL reports one
LangGraph timeout and six ADK recovery failures (five timeouts and one strict
provider-count mismatch); all 44 new child cases pass on each principal.
Manual restart fixtures now use the existing explicit-delivery isolation
fixture, preventing shared-datasource background work from using their local
fakes. The logs do not identify a specific foreign invocation. All original
assertions and ten-second bounds remain. Focus passes 29 cases per actual
principal; fresh PostgreSQL qualification remains required.

The Windows timing job at that source passes its first two files (55 cases),
then reaches its limit because one over-indented YAML line preserves a newline
in the folded command. PowerShell consequently receives a separate Python-file
launch instead of the remaining pytest arguments. The corrected indentation
and regression guard retain every selected fixture and the twenty-minute bound.
Seven CI workflow guards pass. Earlier failed/cancelled runs remain historical;
neither partial pass establishes complete platform qualification.

The frozen behavioral batch passes all four independent environments:

| Profile | Passed | Declared skips | Existing expected failures |
| --- | ---: | ---: | ---: |
| Root | 2,320 | 1,224 | 2 |
| Actual ADK, shared contracts and CTK | 1,300 | 879 | 2 |
| Actual LangGraph, shared contracts and CTK | 1,339 | 852 | 2 |
| Optional native Framework, shared contracts and CTK | 1,639 | 1,229 | 2 |

Root coverage is 90.79%, above the unchanged 90% requirement. All 362 frozen
inputs remain unchanged throughout each full run; no run times out. Formatting,
lint, strict typing, documentation links and all five package builds pass.
The principal CTK subsets each pass all 96 checks. Backend/foreign-native skips
remain explicit and establish no unavailable native/database capability.

Additional finite proofs cover waiting-child END and private context/export
isolation, resolved-latest version pinning after reopen, two real runtime
dispatchers, actual lease takeover and stale native/common writer rejection.
Six synchronous and six detached process-stop boundaries run per principal,
including task/workflow deadlines after producer termination. The original
deadline, identities and saved quotas survive; expired parents fault with 408,
cancel synchronous children and perform zero actions. Detached children keep
their independent lifetime. Known external outcomes are retained once;
unresolved external leaf intents remain uncertain and are never adopted by
internal child reconciliation.

An additional packaging check found the standalone core lock missing already
declared timer dependencies and prior security fixes. Its targeted correction
adds croniter, updates tzdata/pypdf and patches urllib3/multidict without changing
declared package versions. The corrected complete optional profile audits 74
dependencies with zero advisories. A fresh standalone core environment passes
151 child/schedule checks, with 72 unavailable PostgreSQL skips. All 32 workflow
and audit guards pass with seven required audit environments and a dedicated
core lock check. The original stale-lock and vulnerable-audit failures are
retained in `tmp/s2-07-core-*`; they are not relabelled as passes.

Raw full-run logs, immutable input hashes and bounded supervisor results are
retained under `tmp/s2-07-full-*`. The full-runtime snapshot and subsequent
standalone-lock/CI correction have separate evidence. Hosted qualification
must verify the final published source, four images, Windows, all seven audits,
CTK provenance, benchmarks and the real PostgreSQL child cases before closure.
General active-worker drain, compensation and ambiguous external-action
reconciliation remain S2-08; compatible version migration remains S2-09.

Working changes now include an atomic child identity/input reservation and saved
root depth/total-child quotas. Siblings consume the same root budget, and policy
changes after reopen do not reset an existing tree's saved limits. Catalog
reuse pins the original child definition and preserves the canonical parent
task reference separately from the internal execution occurrence.

Detached execution has an atomic durable job/parent-return decision. The
parent returns transformed task input; the child retains an independent
lifetime and separate common identity. A bounded dispatcher uses the existing
engine ownership and native admission paths, and leaves armed child timers to
their exact original timer delivery. The finite quota/corruption, recursion
and detached lifecycle/reopen fixtures pass the `3f568e5` checkpoint, including
actual PostgreSQL. Synchronous nonresident child waits, durable cancellation
and process/corruption fixtures are implemented locally. Focused native checks
pass their first batch; full, competing-worker, context/version and exact-source
qualification is still required.

Authority: Project Definition.md, TODO S2-07, inventory A034-A036 and T09-T11.
Next-batch focused evidence: n=3 creates four distinct invocation/session
identities with pinned version, canonical ancestry and inherited principal/user.
Depth and total-child limits reject excess work before base-case model dispatch.
Each principal SDK passes all three recursion cases. Queue corruption tests
preserve damaged identity evidence, reject its dispatch and allow a later valid
child to complete once. Each principal SDK passes 21 focused queue/budget/
detached cases; root passes 26 with 16 unavailable PostgreSQL cases skipped.
The full checkpoint at `3f568e5` passes all six configured workflows. Actual
PostgreSQL passes two detached and three recursion/quota cases per principal;
the new common budget fixtures also execute on the real database. These
qualify only their finite child subset. CH05 and complete CH01-CH10 acceptance
remain open.

The unchanged schema admits run.workflow and run.await. The pinned
[OWS reference](https://github.com/open-workflow-specification/specification/blob/9b5b1da29e9d4fff2358580241e11aab22704a16/dsl-reference.md#run)
requires asynchronous run to return transformed input without waiting for or
inventing the child result. Bootstrap policies and durable common metadata
must preserve separate identities; engine checkpoints stay private.

| Requirement | Required proof |
| --- | --- |
| CH01 | Explicit child version and resolved fingerprint remain pinned across parent recovery and latest catalog changes. Distinct invocation/session, canonical parent task reference, user correlation and authenticated owner are preserved. |
| CH02 | Nested synchronous output, null, fault, END and exported/context data preserve parent continuation without leaking private child context. Existing terminal dependency corruption tests remain enabled. |
| CH03 | Self-recursion with n=3 creates exactly four invocation identities and the base-case result. Mutual recursion terminates or reaches an explicit common policy error. |
| CH04 | Deployment depth and total-child-work quotas are enforced before new execution, across siblings, recursion and reopen. Repeated recovery cannot reset or spend the same reservation twice. |
| CH05 | Waiting synchronous children release parent execution without reporting a child fault. Original deadlines and parent/child cancellation remain effective during suspension and after process stop. |
| CH06 | run.await=false commits a durable child identity/input/definition binding before returning transformed task input. Child output is never fabricated into parent output. |
| CH07 | Detached work continues after successful parent completion and worker restart. A native task/promise alone is not durable dispatch. Parent cancellation and shutdown behavior follow explicitly documented lifetime policy, with no unnoticed orphan execution. |
| CH08 | Atomic child reservation, dispatch and terminal observation survive crashes before/after each boundary. Two workers and stale generations cannot create duplicates or replay known side effects. SQLite and actual PostgreSQL are required. |
| CH09 | Missing/changed bindings, input, identity, parent, budgets, native checkpoint or terminal dependency fail closed before model/action dispatch and before mutation of known state. |
| CH10 | Relevant shared, real ADK/LangGraph and process-stop tests, optional AF bounded behavior, quality/build/schema, configured PostgreSQL/Windows/images/security and exact capability limits pass before task closure. |

Historical reviewed source e56e5b8 has separate synchronous common child identity,
terminal child dependency receipts, immutable local catalog versions and known
outcome corruption guards. It explicitly rejects background run at admission.
At that source finite self-recursion works, but no recursive depth/total-child policy exists. Synchronous run waits on the child
invocation and treats nonterminal status as a fault, so nonresident child wait
needs dedicated integration. This is an actionable gap, not a deferred blocker.

Native baseline on unchanged source: LangGraph existing child dependency,
event/child effect recovery and catalog tests: 88 passed, 74 locally unavailable
profile skips, 43.96 seconds. Independent actual ADK passes the same 88 cases, with 74 locally unavailable profile skips, in 56.75 seconds. These existing
tests do not prove new CH01-CH10 behavior or actual PostgreSQL locally.

Broader cross-version migration belongs to S2-09; general compensation and
ambiguous external-action resolution to S2-08; general background protocol
adapters to S3-03; full proposed/JS qualification remains Stage 4. Preserve
those owners and default one-container embedded operation.

An untracked finite countdown probe passes on each actual principal SDK: n=3 produces four distinct invocation/session identities, the n=0 result, original principal/user correlation and pinned fingerprint, canonical parent reference and zero model calls. Each environment runs two adapter cases; its own SDK case is native, the other is an unavailable-SDK fallback. This establishes the finite recursion baseline only, without qualifying quotas or detached execution.
