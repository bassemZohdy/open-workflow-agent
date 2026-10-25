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
