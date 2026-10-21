# Earlier backlog delivery checkpoints

This preserves earlier project progress notes; later notes supersede earlier task counts and next-task statements. See [current status](../../PROJECT.md).

## Current backlog delivery mode — 2026-10-13

S3-04 has a source-authored finite executable-source and awaited-process control
batch, documented in `docs/s3-04-executable-source-acceptance.md`. Original public
source references and private archived bytes are bound independently to actual
business receipts. Imported catalog executable sources remain unsupported. The
first coherent behavioral/native/process/database/platform and publication gates
are pending; S3-04 and its feature rows remain incomplete. This authoring does not
replace the existing deployment or real-cluster acceptance gates.

At the user's request, remaining changes are being integrated locally under
`codex/required-backlog` before one final comprehensive validation pass.
The latest instruction requests a push and complete CI/CD checkpoint for the
current batch, followed by continued implementation. Subsequent work retains
the preference for batched validation instead of a full CI cycle per task.
The first batch was pushed at `2c6d5e6`. Quality/native/image/security checks
passed, but PostgreSQL ADK recovery reached 89% before its nine-minute command
limit, with no preceding assertion failure. Database profiles are now split
into independent bounded jobs, preserving every selector and requiring all
profiles before both database images. Windows routing, effect/ownership and
timer/event recovery are similarly separated while retaining their selections
and twenty-minute limits. All six workflows pass for the correction at `3f568e5`.
CI cleanup is verified for this subset: feature pushes no longer duplicate PR CI/audits,
root contracts run once within the full suite, independent native/image checks
run in parallel, and optional Framework/Docs checks have stale-PR cancellation.
Common changes now trigger optional Framework acceptance. Existing exact-source
release, security and backend gates remain in place. Checkpoint validation is
green at `3f568e5`; historical run evidence remains unchanged.
Small checks may resolve specific implementation issues, but task completion
requires the final relevant behavioral, native, database, platform, build,
security and image evidence. No unchecked task is promoted by this batching.

S2-06 runtime source `e56e5b8` passed PR CI, PostgreSQL, optional Agent
Framework, external sandbox, Security and Docs. PostgreSQL passed 256 core,
343 LangGraph, 309 ADK and 147 optional AF cases, including all 42 new timer/
scheduling cases per principal and both database-backed images. Its cancelled
push Windows job was rerun independently and passed. The scoped capability/API/image
assertion changes pass final batch acceptance at `3f568e5`. S2-07 has atomic child identity/input
reservation and persisted root depth/total-child quotas. Durable detached job
decisions and bounded engine-owned delivery qualified for their partial profile. Nonresident
parent/child waits, cancellation, queue corruption and crash qualification remain
open. Fifteen of 37 tasks are verified; no task is deferred.
Further S2-07 changes now reject individual damaged queue identities while
allowing later valid children to run. Finite n=3 recursion and depth/count
rejection pass on actual ADK and LangGraph, including PostgreSQL. These are partial proofs;
nonresident child waits, cancellation and full crash acceptance remain open
at that published checkpoint.

The current S2-07 batch implements parent-owned immutable wait/wake receipts,
private ADK/LangGraph child suspension and verified fork reentry, preserved
absolute deadlines, synchronous cancellation cascades and bounded recovery
after process stop. Common stores retain identities and policy evidence;
engine-native checkpoints remain private. Full root, actual principal native,
optional Framework and CTK suites pass the frozen batch, including terminal
provenance, context/version isolation, competing workers and original deadlines
after actual process stop. Root coverage is 90.79%. Standalone core passes its
corrected lock, finite child/schedule checks and complete dependency audit;
the CI now requires seven audit environments. Final source `6b23e5d` passes
all six hosted workflows, all four runtime images, seven zero-advisory reports,
both native benchmarks and canonical CTK provenance. Actual PostgreSQL passes
all 44 child and 42 scheduling cases per principal. S2-07's finite CH01-CH10
profile is verified; sixteen of 37 tasks are complete. S2-08 is next and no
task is deferred. Earlier failure and cancellation evidence remains historical.
See the [child acceptance record](../s2-07-child-workflow-acceptance.md).

The isolated S2-08 batch adds atomic authorized cancellation and lease
revocation, separates host drain from business cancellation, and preserves
uncertain external action intents. Repeated logical HTTP operations receive
distinct keys while retries retain the original admitted key. Optional trusted
receiver lookup uses the same bounded authenticated HTTPS client and never
repeats the business request during recovery. Compensation remains an explicit
workflow operation. The finite LC01-LC08 profile is verified at exact source
`96ce610`: all six hosted workflows, all four runtime images, seven zero-advisory
audits, both native benchmarks and canonical CTK provenance pass. Both principal
SDKs pass all 46 new lifecycle cases on SQLite and actual PostgreSQL, including
real producer termination, native corruption, active child controls and stale
ownership. Root passes 2,419 cases with 90.52% coverage; main native suites pass
1,204 ADK and 1,243 LangGraph cases plus 96 CTK checks each. Optional Framework
passes 1,698 cases without a production image or principal durability claim.
The first stopped batch and later failed fixture observations remain historical;
their original results are preserved in the
[lifecycle acceptance record](../s2-08-lifecycle-acceptance.md).
Seventeen of 37 tasks are verified. S2-09 is next and no task is deferred.
No automatic rollback, general exactly-once action guarantee, full proposed
criteria or independent JavaScript qualification follows from this finite profile.
