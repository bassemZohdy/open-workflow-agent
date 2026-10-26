# Main integration checkpoint — 2026-10-25

Integration scope: [PR #64](https://github.com/bassemZohdy/open-workflow-agent/pull/64).
Merged into main as `f013a2ec1ca7a471cb35e01329c5880b467b626b`.
The integration includes the accumulated runtime work, task records, optional
automation controls, Docker cache ordering and the final native recovery fixture
repair. It is not a release, deployment or claim of complete conformance.

## Accepted task baseline

**31/37 tasks are verified; six remain open.** Eleven of the original 17 open
tasks have verified finite scopes. Eight qualify at `d72781f`; S3-03, S4-05 and
S4-07 qualify at `f66cd23`. No proposed/schema inventory row is promoted by
this integration. S4-02, S4-03 and S4-04 remain ready; S4-09, S4-10 and S4-11
remain conditional at the user's instruction. None is deferred.

## Current validation, separate from the accepted baseline

Frozen candidate `5310f5aa2916c150bb33ae889ba65bc480bed5c1` has these manual
qualification runs. Their final results belong to that frozen source, not the
later merge or its fixture repair.

| Workflow | Run | Final frozen-source result |
| --- | --- | --- |
| CI | [40193137742](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193137742) | All 22 jobs pass, including actual three-runtime literal outcome comparison. |
| PostgreSQL | [40193140146](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193140146) | Ten jobs pass; native LangGraph fails four fixture setup checks described below; its dependent runtime-image job is skipped. |
| Optional Framework | [40193142201](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193142201) | Passed. This does not activate Framework production scope. |
| External sandbox | [40193144264](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193144264) | Passed. |
| Security | [40193146652](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193146652) | Passed. |
| Docs | [40193148695](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193148695) | Passed. |

CI reports **5,236 root passes, 3,306 skips and 90.25% coverage**. The actual
ADK/LangGraph shared suites pass **1,489 / 1,573 cases** (1,547 / 1,515 explicit
opposite/unconfigured-profile skips), plus **96 selected CTK cases each**.
The required literal aggregate job `121177010650` passes across actual ADK,
LangGraph Python and Node with matched source/runtime/SDK/fixture/schema evidence.
This verifies only the named 13-case comparison, not public identity, durability
or any full proposed/schema criterion. Earlier intermittent scoped-flow/timer
observations remain in the retained history; the corresponding current CI jobs
pass without relaxing their original deadlines.

The native PostgreSQL failure occurs after the producer has saved its durable
checkpoint. The test's `ProducerIdentity` helper assumes `os.pidfd_open` exists;
the hosted Python build lacks it. The repaired fixture launches Python directly,
asserts that the acknowledged worker PID equals its retained child PID, and
stops that child through `Popen.kill()`. It never signals an arbitrary reported
PID. The original durable boundary, abrupt-stop, privacy, fresh-process recovery,
single reducer contribution and duplicate-terminal-resume assertions remain.

The repair passes **all eight SQLite/PostgreSQL × Command/default × root/nested
cases on Linux, with zero skips**, while both parent pidfd APIs are deliberately
unavailable. It also passes **all four PostgreSQL cases on Windows**, with no
skips. The canonical tested fixture SHA-256 is
`84ac99f97e4a2db13f58230b1480c1cea6d9c9ad5ac6c91ae8f0aced6333b9f1`.
The Linux runner uses unchanged pinned test dependencies and a read-only source
mount; it installs nothing at startup. Raw evidence is retained under
`tmp/merge-20261025/` and `tmp/javascript-sandbox-20261025/`.

This repairs a test portability gap; it is not a waiver or an additional runtime
capability. No full green result is claimed for the repaired integration source
until its own hosted qualification completes. Automatic validation and automatic
publication remain separately opt-in; release source/version/image gates remain
intact.

## Next work

Continue **S4-02**, starting with the native memory slice. Reuse the installed
LangGraph store APIs behind a bounded, private adapter boundary. Keep long-term
memory separate from checkpoint/session state; bind any future public access to
original common authority. Do not advertise the capability until workflow
admission, identity isolation, persistence/recovery and common receipt/ownership
tests pass. Streams, public state updates, replay and time travel remain open.

The complete original ready-task notes are retained in the
[pre-integration history](pre-integration-open-tasks-2026-10-25.md).

## PR #76 integration — private native memory foundations

[PR #76](https://github.com/bassemZohdy/open-workflow-agent/pull/76) merged on
2026-10-25 at `56e68b048c2d67c9216956962a9f49fe1fa9527d`, preserving head
`659dae1b10656515e7e094bcd1367636db303980`. Local `main` was fast-forwarded to
that commit and its push confirmed GitHub was already up to date. The checkout
was clean before the next feature branch was created. No unrelated dependency
PRs, release flags or repository protections were changed.

The required hosted Markdown check passed on the exact PR head in
[Docs run 40225187186](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40225187186)
before the merge. Automatic CI remains disabled as requested. The private
memory implementation retains its recorded 169-case Windows/frozen-Linux
qualification at `f76fed9`; this merge is not a full repository or release
qualification and does not enable a memory capability. S4-02 stays open, with
the task total unchanged at 31 of 37 verified.

Work continues from this main commit on `codex/s4-02-memory-write-recovery`,
integrating common write intents/completions and native evidence verification.
The current state and remaining gates are recorded in the
[native memory acceptance record](../s4-02-native-memory-acceptance.md).

## PR #77 integration — 2026-10-26 Asia/Dubai

[PR #77](https://github.com/bassemZohdy/open-workflow-agent/pull/77) merged at
`df8c0d7c6ee753e25470f4e150cd835dbca0980b`, preserving head
`496b977cf6c4fd229fc1365ee0e7cdbcbf9d5378`. GitHub records the merge at
2026-10-25T21:21:57Z, which is 2026-10-26 locally. Local `main` was fast-forwarded
to the merge and its push confirmed GitHub was up to date.

The required hosted Markdown check passed on the exact PR head in
[Docs run 40235040643](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40235040643).
No protections or automatic CI/release flags were changed. The accepted finite
implementation evidence remains the 322-case Windows/frozen-Linux cohort at
`54103b9`; the merge does not claim a full repository/release qualification.
The backlog stays 31/37 verified and S4-02 remains open.

The next ready work starts from this main commit on
`codex/s4-02-memory-retention`, adding database-timed record expiry while
preserving common observations and native/common completion evidence.

## PR #78 integration — 2026-10-26 Asia/Dubai

[PR #78](https://github.com/bassemZohdy/open-workflow-agent/pull/78) merged at
`b3440589c64388aab4f14129fb014276cd71da1f`, preserving head
`f350c34f8ffbb49f5b98111ebedb3621ad15a20a`. GitHub records the merge at
2026-10-25T21:50:13Z (2026-10-26 locally). Local `main` was fast-forwarded and its
push confirmed GitHub was up to date. The exact-head required check passed in
[Docs run 40237052018](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40237052018).

The retained finite evidence is the 355-case Windows/frozen-Linux cohort at
`810c723`. This does not establish full repository/release qualification or
enable public native memory admission. No protections or automatic CI/release
flags changed. S4-02 remains open and the verified count stays 31/37.

Work continues on `codex/s4-02-memory-capacity`: namespace record/value budgets,
bounded authorized physical cleanup, and retention of common/native proof.

## PR #79 integration — 2026-10-26 Asia/Dubai

[PR #79](https://github.com/bassemZohdy/open-workflow-agent/pull/79) merged at
`3187cfe4e9728a419829a38fab04de5a76221a01`, preserving head
`bba47b3bc442510bb7c4edd5b12464deff45ca0f`. GitHub records the merge at
2026-10-25T22:29:15Z (2026-10-26 locally). Local `main` was fast-forwarded and its
push confirmed GitHub was up to date. Its required exact-head check passed in
[Docs run 40240383097](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40240383097).

The finite namespace-capacity/physical-cleanup evidence remains the 399-case
Windows/frozen-Linux cohort at `6a0ac8c`, with eight shared authority contracts.
Public native memory admission remains closed; this merge does not qualify a
full repository/release or complete S4-02. The task count stays 31/37 verified,
and no protections or automatic CI/release flags changed.

Work continues from this main commit on `codex/s4-02-memory-provider-lifecycle`
with simultaneous first-use migration, bounded setup waits, shutdown errors and
cleanup after cancellation or abrupt process exit.

## PR #80 integration — 2026-10-26 Asia/Dubai

[PR #80](https://github.com/bassemZohdy/open-workflow-agent/pull/80) merged at
`1a0b0a95e2110df045cef2838d0e1acc78314b96`, preserving head
`d599587039cabe794105c4703df71189fbf90ce3`. GitHub records the merge at
2026-10-25T22:40:27Z (2026-10-26 locally). Local `main` was fast-forwarded and
its push confirmed GitHub was up to date. The required exact-head check passed
in [Docs run 40241216746](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40241216746).

The retained finite provider lifecycle evidence is the 415-case Windows/frozen
Linux cohort at `738b393`, plus eight shared authority checks. Native memory
admission remains closed; this is not full repository/release qualification or
completion of S4-02. The count stays 31/37 verified. No protections or automatic
CI/release flags changed.

Work continues from this main commit on `codex/s4-02-memory-resolution` with
explicit recorded completion from native proof or acknowledged abandonment of
an unknown outcome, followed by authorized public controls and workflow bindings.

## PR #81 integration — 2026-10-26 Asia/Dubai

[PR #81](https://github.com/bassemZohdy/open-workflow-agent/pull/81) merged at
`9c1166107e48e35b6fb5e92d8a6504d6145bc9cd`, preserving head
`dcf7e49336679ec35ddc0572f122455e9204963b`. GitHub records the merge at
2026-10-25T23:17:36Z (2026-10-26 locally). The required exact-head check passed
in [Docs run 40244018544](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40244018544).

Runtime source `602449e` passed the 523-case Windows/frozen Linux memory,
ownership, resolution and native-checkpoint cohort, plus eight shared authority
checks on each platform with fresh PostgreSQL databases. This merged private
control and checkpoint migration repair did not enable public memory admission
or complete S4-02. The backlog remains 31/37 verified, with three ready and
three conditional tasks. No protections or automatic CI/release flags changed.

Work continues from this main commit on `codex/s4-02-public-memory`, connecting
versioned workflow calls, filtered search and original-owner recovery controls
to the merged common evidence and native stores.

## PR #82 integration — 2026-10-26 Asia/Dubai

[PR #82](https://github.com/bassemZohdy/open-workflow-agent/pull/82) merged at
`5121cc3ac0192cc1f8fae2ec6b94a57fe07edb84`, preserving head
`1f56d94cca6f9ceab6df54d78a7c5754b38e2c81`. Its exact-head required check passed
in [Docs run 40251702766](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40251702766).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Runtime source `c062e9c` qualifies 588 memory/public-call/native checkpoint cases,
eight separate cold LangGraph authority cases and 46 actual ADK native/authority/
fencing cases on each of Windows and frozen Linux, with zero skips in those
selections. SDK-free core/API/configuration checks add 266 passes and 91 expected
optional-environment skips. All 1,211 frozen Linux inputs remain unchanged.

The merge enables only configured `langgraph.memory@1.0.0` bindings, scalar-filter
search and original-owner recovery controls. It repairs early unsupported-loop
admission and concurrent cold ADK PostgreSQL metadata setup. It is not a full
repository/release qualification or completion of S4-02. Counts remain 31/37
verified, six open, none deferred; no protections or automation flags changed.

Work continues from this main commit on `codex/s4-02-semantic-search` with the
declared local-model/index contract, native vector transactions and versioned
semantic queries under the same original authority and common recovery proof.

## PR #83 integration — 2026-10-26 Asia/Dubai

[PR #83](https://github.com/bassemZohdy/open-workflow-agent/pull/83) merged at
`872d424efc966303561cc10fef8ce17603451964`, preserving head
`b60551d829d079996c582fd885abd06967991593`. The required exact-head check passed
in [Docs run 40258852861](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40258852861).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Runtime `a781bc2` passes 638 combined memory/native-checkpoint cases on each of
Windows and frozen Docker/Linux with zero skips and 1,222 unchanged Linux
source inputs. Separate fresh-database authority checks pass eight actual
LangGraph and eight actual ADK cases on each platform. The real packaged-model
HTTP example passes on SQLite with networking disabled and on pgvector, with
no external model calls. Core/API checks add 287 passes and 91 expected optional
environment skips. Earlier failed attempts remain in the acceptance record.

This enables only the declared fixed local-model/exact-index semantic profile;
broader memory gates and S4-02 remain open. Counts remain 31/37 verified, six
open and none deferred. No protections, automation flags or published images
changed. Work continues on `codex/s4-02-native-streams` with private bounded
observation transport and safe projection from the actual pinned SDK stream.

## PR #84 integration — 2026-10-26 Asia/Dubai

[PR #84](https://github.com/bassemZohdy/open-workflow-agent/pull/84) merged at
`73778d6025a2d54939a918e5a3acab6b19671dcf`, preserving head
`39bbecc736f822f7edcd24e1c8feafd52dadd0aa`. The required exact-head check passed
in [Docs run 40259950435](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40259950435).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Private stream source `baebb73` passes 106 native/authority/fencing/transport
cases on each of Windows and frozen Docker/Linux with zero skips and 1,226
unchanged Linux source inputs. This merge exposes no public stream capability.
S4-02/T50 remain open. Work continues on `codex/s4-02-native-stream-api` with
opt-in deployment policy, original-owner HTTP observation and explicit
admission, retention, resource, disconnect and restart checks.

## PR #85 integration — 2026-10-26 Asia/Dubai

[PR #85](https://github.com/bassemZohdy/open-workflow-agent/pull/85) merged at
`2f33a3b9422e960273628f881dcb34606037bb28`, preserving head
`36ed0f5ec4abcba874750df6b0965dbf45fe5c6d`. Its required exact-head check passed
in [Docs run 40263591960](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40263591960).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Runtime `a597134` passes 318 combined stream/native/ownership/write-recovery
cases and 18 separate actual ADK authority/fencing/admission cases on each of
Windows and frozen Docker/Linux with zero skips and 1,237 unchanged Linux
inputs. The SDK-free baseline passes 242 cases; its 46 omitted PostgreSQL cells
pass a separate fresh-database run, closing those skips. The acceptance record
retains the initial startup-order failure and intentionally stopped duplicate.

The merge adds only the opt-in original-owner values/updates source profile and
repairs Windows fenced-write worker loop selection. It does not publish images
or complete S4-02/T50. The count stays 31/37 verified, six open, none deferred;
protections and automation flags are unchanged. Work continues on
`codex/s4-02-native-state-controls` with bounded state/history reads as the
prerequisite for exact compare-and-update and safe branch management.

## PR #86 integration — 2026-10-26 Asia/Dubai

[PR #86](https://github.com/bassemZohdy/open-workflow-agent/pull/86) merged at
`26496464194bdb86d606e30d8b6357062f537f20`, preserving head
`9cfc97df2516e73f19327d523a53556b19403352`. Its required exact-head check passed
in [Docs run 40269512911](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40269512911).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Runtime `f060349` passes 285 combined state/history/native cases and 20 separate
actual ADK authority/fencing/rejection cases on each of Windows and frozen
Docker/Linux, all with zero skips and 1,250 unchanged Linux inputs. SDK-free
configuration/API/recovery/tracking adds 312 passes with zero skips. Read-only
views expose bounded projected data and opaque references; they do not authorize
continuation, mutation or replay. S4-02/T45/T49 remain open. Work continues on
`codex/s4-02-native-state-updates` with atomic SDK checkpoint/write/proof storage,
then common management intents, policy admission and recovery reconciliation.

## PR #87 integration — 2026-10-26 Asia/Dubai

[PR #87](https://github.com/bassemZohdy/open-workflow-agent/pull/87) merged at
`a4462d052dd367527123e25d33e3e66b7b215759`, preserving head
`77910d034fb891bfbe2713074041b1da313cbce8`. Its required exact-head check passed
in [Docs run 40270673685](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40270673685).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Private atomic updater source `3bf27d7` passes 99 SDK/state-read/fencing/admission
cases per platform on Windows and frozen Docker/Linux with zero skips and 1,253
unchanged Linux source inputs. Its process-exit fixture proves rollback before
the native commit. The component enables no API route, advertised mutation
capability or automatic storage setup. S4-02/T45/T49 remain open. Work continues
on `codex/s4-02-state-control-journal` with common authorized intents and recovery
from real native proof, followed by declared policy/schema/task attribution and
native continuation/branch gates.

## PR #88 integration — 2026-10-26 Asia/Dubai

[PR #88](https://github.com/bassemZohdy/open-workflow-agent/pull/88) merged at
`2d972b01b759c6e3389cfb20204228c47de5e141`, preserving head
`bd612273b3aa5a7b88a0ee94d7bcd4833eefe342`. Its required exact-head check passed
in [Docs run 40272750899](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40272750899).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Private control journal source `7572808` passes 174 combined cases and 20
separate actual ADK authority/fencing/rejection cases per platform, plus 653
SDK-free core/API cases, all with zero skips and 1,257 unchanged Linux inputs.
The common journal recovers real native commits without duplicate SDK calls;
dispatched controls without proof remain unknown. No mutation route or capability
is enabled. S4-02/T45/T49 remain open. Work continues on
`codex/s4-02-state-update-policy` with compiled policy/schema/attribution checks
and read-only preflight of actual OWA native state and retained scope evidence.

## PR #89 integration — 2026-10-26 Asia/Dubai

[PR #89](https://github.com/bassemZohdy/open-workflow-agent/pull/89) merged at
`f931945a96b84fb3ed0f3172d8d86095ef65489b`, preserving head
`6bb63fe827cf53759d31d004663f24c4b04dfe2b`. Its required exact-head check passed
in [Docs run 40275938589](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40275938589).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Private policy/preflight source `8393d1e` passes 278 combined cases and 22
separate actual ADK cases per platform, plus 755 SDK-free compiler/core/API cases,
all with zero skips and 1,262 unchanged Linux inputs. The compiler validates
bounded schema/task/policy declarations; read-only preflight checks actual
suspended OWA state, common receipts and completed scope evidence. Required
mutation admission stays closed. S4-02/T45/T49 remain open. Work continues on
`codex/s4-02-state-update-attestation` with atomic SDK data replacement, versioned
common/native attestation and private continuation/recovery checks.

## PR #90 integration — 2026-10-26 Asia/Dubai

[PR #90](https://github.com/bassemZohdy/open-workflow-agent/pull/90) merged at
`1e83e14b7646572dcf982b9c3ea184eb908e04e1`, preserving head
`32a0a6a301ac1e03cc2cdad13316c863f26750dc`. Its required exact-head check passed
in [Docs run 40281799381](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40281799381).
Local main was fast-forwarded and its push confirmed GitHub was up to date.

Private attestation source `1a546e6` passes 428 combined cases and 22 separate
actual ADK cases per platform, plus 763 SDK-free core/API cases, all with zero
skips and 1,266 unchanged Linux inputs. The SDK update changes only OWA data,
preserves history/routes/counters/channels and binds the exact base and result
to versioned common/native evidence. Private continuation and fresh-process
recovery pass; public mutation admission stays closed. S4-02/T45/T49 remain open.
Work continues on `codex/s4-02-state-control-resolution` with authorized immutable
completion/unknown-outcome decisions, followed by public configured ingress.
