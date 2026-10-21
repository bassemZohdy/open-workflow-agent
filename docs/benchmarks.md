# Matched performance measurements

The S4-06 matched profile is separate from the quick runtime and engine smoke
probes documented in [development](development.md). It runs the same
deterministic fake-model workflow for ADK and LangGraph with SQLite and actual
PostgreSQL, using locked engine environments. No paid provider or external
business endpoint is used.

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
