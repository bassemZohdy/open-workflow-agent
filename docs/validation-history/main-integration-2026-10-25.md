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
