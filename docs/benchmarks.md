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

The required PostgreSQL CI workflow runs the four cells on named Ubuntu
runners and assembles one `s4-06-matched-performance-report` artifact. The JSON
records the commit, Python version, engine lockfile SHA-256, selected engine and
backend, runner identity/platform, and measurement parameters. The PostgreSQL
DSN is used only for the connection; credentials and the DSN are never written
to the report.

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
