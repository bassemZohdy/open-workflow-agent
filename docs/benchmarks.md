# Matched performance measurements

## Exact-source qualification d72781f

S4-06 is verified for its finite profile at `d72781f86053a7fd77e7b9df7c3a74c262923136`. Retained matched ADK/LangGraph x SQLite/PostgreSQL 30/5/8/3 benchmark report with latency, invocation CPU, lifetime peak RSS, concurrency, creation/footprint and timed service restart/resume correctness.

Runner-specific measurements are not capacity guarantees; persistence I/O is included in latency, PostgreSQL size is whole-service and recovery timings use same-process reinitialization. Physical-stop proof remains separate.

All six source-matched workflows pass; [the retained results](validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f) include actual principal SDKs, both databases, platform/process, image, security and packaging checks. This is not full OWS/proposed-test conformance.

The S4-06 matched profile is separate from the quick runtime and engine smoke
probes documented in [development](development.md). It runs the same
deterministic fake-model workflow for ADK and LangGraph with SQLite and actual
PostgreSQL, using locked engine environments. No paid provider or external
business endpoint is used.

The matched-harness and aggregate-guard tests passed in the corrected
consolidated local batch. A fresh four-cell hosted report for that exact source
batch remains pending; retained measurements below are from an earlier exact
source and do not substitute for requalification.

Each engine/backend profile performs five warm-up invocations, thirty measured
sequential invocations, one eight-invocation concurrent wave, and three
restart/resume samples. It emits nearest-rank p50/p95 and mean latency, measured
process CPU time for the sequential/concurrent invocation phases, throughput,
common invocation-record creation latency, database footprint before/after, and timed suspend
and engine/service reinitialization plus resume. Recovery must complete without replaying the fake
model or the side-effect receiver, and the operation identity must remain
stable.

The concurrent wave is a bounded observation of eight simultaneous runtime
invocations, with per-invocation latency and wave throughput. The harness
rejects concurrency settings above 16 so an accidental CLI override cannot
turn this hosted check into an unbounded load test. The fixed workload uses the
same deterministic fake model and workflow in each of the four cells. This is a
runner-specific comparison at the selected concurrency; it does not establish
production capacity, saturation behavior, or a deployment limit. Runner CPU,
database service sizing, and scheduling can affect the observed numbers.

The required PostgreSQL CI workflow runs the four cells on named Ubuntu
runners and assembles one `s4-06-matched-performance-report` artifact. The JSON
records the commit, Python version, engine lockfile SHA-256, selected engine and
backend, runner identity/platform, and measurement parameters. The PostgreSQL
DSN is used only for the connection; credentials and the DSN are never written
to the report.

## Retained exact-source measurements

### Follow-up at `d72781f` — 2026-10-25

All four matched cells and aggregation passed at
`d72781f86053a7fd77e7b9df7c3a74c262923136`. The unchanged
[raw report](benchmark-results/d72781f-matched.json) is retained from the
[hosted artifact](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378766).
The ZIP matches GitHub's SHA-256 digest
`3c90ac05ea6af3dc88fcca2200670f3ad9732eebb694ad479d22221c51b9550d`,
and the repository aggregator validates its complete source/workflow/parameter
matrix. Corrected runner names identify `GitHub Actions 1000028672` (ADK) and
`GitHub Actions 1000028681` (LangGraph); both are Linux x86_64, Python 3.12.13.
Parameters remain 30 sequential measurements, five warm-ups, concurrency eight
and three recovery samples per cell.

| Engine | Backend | Sequential p50 / p95 (s) | Concurrent throughput (/s) | CPU (s) | Peak RSS (MiB) | Persistence delta (bytes) | Resume p50 (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ADK | SQLite | 0.340 / 0.380 | 2.09 | 14.84 | 184.40 | 1,187,840 | 0.498 |
| ADK | PostgreSQL | 0.851 / 1.121 | 1.11 | 27.40 | 253.56 | 2,670,592 | 1.505 |
| LangGraph | SQLite | 0.299 / 0.316 | 2.46 | 12.97 | 159.46 | 2,121,728 | 0.409 |
| LangGraph | PostgreSQL | 0.547 / 0.781 | 1.72 | 17.16 | 125.45 | 3,473,408 | 0.963 |

All twelve recovery samples complete with one fake-model call, one side effect
and one operation identity each. The fresh complete PostgreSQL workflow also
passes both operation suites and runtime-image persistence acceptance. Full
source CI remains pending at this recording point. Runner-specific, footprint,
RSS and same-process recovery limitations below remain applicable; these numbers
are not production capacity or cross-run performance rankings.

### Follow-up at `88ca0f5` — 2026-10-25

The four matched cells and aggregate passed at
`88ca0f515a3f00bf44e896bac621cf62cb32179a`, using the corrected dependency locks,
Python 3.12.13 and the same 30/5/8/3 measurement parameters. The
[raw report](benchmark-results/88ca0f5-matched.json) is retained from the
[hosted artifact](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40169384763).
The downloaded ZIP matches its GitHub SHA-256 digest
`3c5176035ccef6e38b59ff2eab9ebebf22734ae176fe42c1d82d5616e5c4c956`.

| Engine | Backend | Sequential p50 / p95 (s) | Concurrent throughput (/s) | CPU (s) | Peak RSS (MiB) | Persistence delta (bytes) | Resume p50 (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ADK | SQLite | 0.421 / 0.476 | 1.61 | 19.43 | 183.62 | 1,187,840 | 0.597 |
| ADK | PostgreSQL | 1.030 / 1.290 | 0.90 | 33.35 | 256.19 | 2,777,088 | 1.846 |
| LangGraph | SQLite | 0.156 / 0.218 | 4.99 | 6.61 | 161.96 | 2,121,728 | 0.230 |
| LangGraph | PostgreSQL | 0.264 / 0.317 | 2.64 | 8.40 | 128.36 | 3,506,176 | 0.480 |

This table uses the separate sequential latency summary, rather than mixing its
30 samples with the eight concurrent samples in the aggregate invocation
summary. CPU still covers both measured phases. All twelve recovery samples
completed with one model call, one side effect and one operation identity each.
The footprint, memory and same-process recovery limitations below still apply.
This benchmark success does not qualify the entire source: its PostgreSQL
operation jobs each failed the role-canary fixture's missing credential grant,
while the other nine PostgreSQL jobs passed. That fixture is corrected in the
following working batch; full exact-source requalification remains required.

### Earlier checkpoint at `4b22e80`

The first hosted matched report was generated at exact source
`4b22e801f4f7f01d475caa03a754dd3ac45bf64a` with 30 measured sequential
invocations, five warm-ups, concurrency 8, and three recovery samples per
engine/backend cell. All four cells completed, each recovery resumed without
replaying the fake model or side effect, and each retained one operation ID.
The complete measurements and runner metadata are in the [hosted report
artifact](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39459079488).

| Engine | Backend | Invocation p50 / p95 (s) | Concurrent throughput (/s) | CPU (s) | Peak RSS (MiB) | Persistence delta (bytes) | Resume p50 (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ADK | SQLite | 0.803 / 1.011 | 2.18 | 14.33 | 188.8 | 1,191,936 | 0.469 |
| ADK | PostgreSQL | 0.811 / 6.643 | 1.20 | 25.86 | 260.3 | 2,670,592 | 1.423 |
| LangGraph | SQLite | 0.152 / 0.194 | 4.53 | 6.38 | 143.6 | 2,125,824 | 0.224 |
| LangGraph | PostgreSQL | 0.268 / 0.326 | 2.79 | 8.25 | 132.6 | 3,522,560 | 0.481 |

The percentile column is the harness's end-to-end invocation measurement,
including persistence. PostgreSQL footprint is the full fresh service database;
SQLite footprint is the combined temporary common/native files. The workflow
artifact is the authoritative raw result, and these runner-specific values are
not deployment performance guarantees.

These measurements compare only profiles from the same aggregate report.
Runner scheduling and host load still affect timings, so the results are not
portable performance rankings. CPU time covers the measured sequential and
concurrent invocation phases. Peak RSS is the process lifetime maximum on
Linux, not an isolated per-phase allocation. SQLite footprint covers all
temporary common/native database files. Each PostgreSQL engine job gets a fresh
service; footprint is the whole service database size before/after the profile,
including its schemas and rows, rather than an attributed per-schema estimate.
Invocation timings include runtime and persistence work; database I/O is not
isolated from engine processing. Recovery tears down and recreates engine and
service instances inside the same Python process; it is not a forced OS-process
stop. Existing process-stop acceptance suites remain the crash-recovery proof.
The existing small CI smoke probes remain useful correctness/regression checks
and do not substitute for this matched profile.
