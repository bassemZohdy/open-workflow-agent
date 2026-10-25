# Main integration checkpoint — 2026-10-25

Integration scope: [PR #64](https://github.com/bassemZohdy/open-workflow-agent/pull/64).
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
qualification runs. This table is an integration-time snapshot; consult Actions
for later results.

| Workflow | Run | Integration-time result |
| --- | --- | --- |
| CI | [40193137742](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193137742) | 17 jobs passed; four remain running. No failed job in this snapshot. |
| PostgreSQL | [40193140146](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193140146) | Seven jobs passed; three remain running; native LangGraph fails four fixture setup checks described below. |
| Optional Framework | [40193142201](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193142201) | Passed. This does not activate Framework production scope. |
| External sandbox | [40193144264](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193144264) | Passed. |
| Security | [40193146652](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193146652) | Passed. |
| Docs | [40193148695](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40193148695) | Passed. |

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
