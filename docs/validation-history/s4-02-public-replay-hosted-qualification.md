# S4-02 public replay: hosted qualification at `566fbf1`

Recorded 2026-11-01. The replay profile ([guide](../native-state-replay.md)) was run on
GitHub-hosted Linux runners by manually dispatching the repository's CI and PostgreSQL CI
workflows on the exact commit `566fbf1b` of branch `codex/s4-02-frozen-qualification`. This
is hosted evidence for that commit; the commit that records it adds only unit tests and
documentation. It qualifies the public replay profile, not S4-02 as a whole.

## Results

| Run | Workflow | Commit | Result |
| --- | --- | --- | --- |
| 41287721786 | PostgreSQL CI | `566fbf1` | 14 of 14 jobs succeeded |
| 41287723957 | CI | `566fbf1` | 21 of 22 jobs succeeded; Root quality gates failed (below) |

- **PostgreSQL native state replay** (new job, ubuntu, PostgreSQL 16) passed: the public replay
  HTTP, process-stop and lifecycle suite, the private coordinator tests, the later-checkpoint
  test and the replay configuration tests, each on a fresh SQLite directory or that database.
  Until this run no replay test had run in hosted CI.
- Both Docker acceptance jobs, both PostgreSQL persistence acceptance jobs, the engine
  matrix jobs, the Windows jobs and the exact-source three-engine literal outcome comparison
  (111 cases across ADK, LangGraph and Node) succeeded.
- One Windows job (`Windows native task checkpoints and routing`) failed once on a 5-second
  workflow-timeout test in `test_reusable_definitions.py`, then passed when re-run on the same
  commit. It is treated as a timing flake, not as evidence of a defect.

## What the hosted runs found

The first hosted run exposed regressions that local runs on Windows could not. All were fixed
and re-run; none is hidden by the table above.

1. **The production container failed to start** (four jobs). The image entrypoint links the
   baked embedding model by its `fastembed` 0.8.0 directory name. The dependency integration
   (#99) had moved `fastembed` to 0.9.1, which resolves the model differently, so any container
   with knowledge enabled died at startup. `fastembed` is pinned back to 0.8.0 in the root and
   engine locks; a later bump needs its own image change and qualification.
2. **The hosted literal-parity capture rejected the new shared fixtures.** Its allow-list still
   covered only `set`, `do` and `raise`. It now allows the sandboxed control-flow kinds (`try`,
   `fork`, `switch`, `for`, jq in `set`); `call`, `run`, `emit`, `listen` and `wait` are still
   rejected and `raise` stays literal-only. The whole capture and aggregate was also run locally.
3. **Fourteen test modules errored at collection in the root environment**, because
   `tests/langgraph` shadows the real `langgraph` package as a namespace package and
   `importorskip("langgraph")` wrongly succeeded. They now guard on `langgraph.graph`.
4. **Two native-only test groups failed where native LangGraph is absent** (the late-resume
   checkpoint cases and the stream projection tests). They pass where the SDK is installed and
   now skip where it is not.
5. **The LangGraph engine job hit its 45-minute cap** and the ADK job had finished at 44 of 45.
   The replay suites now run only in their dedicated job, and the engine matrix caps are 60 and
   65 minutes. A CI guard test asserts those reviewed values.
6. **The root type check failed on `psycopg` imports** in the native memory modules, which exist
   only in the `postgres` extra. The type-check step now installs that extra.

## Not resolved: the root coverage gate

The Root quality gates job still fails, and every test in it passes (6,546 passed, 0 failed).
Its 90% line-coverage gate reports 89.12%. Without the replay route module the total would
still be about 89.2%, so the shortfall predates the replay work: modules such as
`state_updates.py`, `execution_authorization.py` and `engine.py` are exercised mostly by tests
that only run in engine environments, and hosted CI was off when they merged. The replay route
now has core-only unit tests (98% of its lines). Whether to lower the gate, exclude
engine-tested modules from it, or run part of the engine suite under root coverage is an owner
decision, listed in [the open decisions](../open-decisions-2026-11-01.md).

## Not claimed

- Windows hosted runs cover the existing Windows job set, not the replay suites.
- This does not complete S4-02; replay is one profile, and broader memory, stream, native
  scope and T45/T49 criteria remain open. Nothing here promotes a research row.
- Branching, nested scopes and non-root checkpoints remain out of scope for replay.
