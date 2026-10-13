# S2-08 lifecycle and external-action acceptance

Status: finite LC01-LC08 Python profile verified at
`96ce610924da4554c84f3c5a687743a48b8e9977`. S2-08 is complete after the
required exact-source native, storage, process-stop, platform and hosted gates.
Only the bounded lifecycle and opt-in trusted HTTP recovery contracts below are
qualified; full proposed criteria, independent JavaScript and general external
exactly-once or automatic rollback guarantees remain unclaimed.

## Final hosted qualification — 2026-10-13

All six configured workflows pass for exact feature source `96ce610`:
[CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38348182681),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38348182693),
[Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38348182669),
[external sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38348182658),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38348182676)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38348182661).

| Hosted profile | Passed | Declared skips | Existing expected failures |
| --- | ---: | ---: | ---: |
| Root | 2,419 | 1,419 | 2 |
| Main actual ADK suite | 1,204 | 879 | 2 |
| Main actual LangGraph suite | 1,243 | 852 | 2 |
| Optional Framework | 1,698 | 1,396 | 2 |
| PostgreSQL common store | 547 | 1 | 0 |
| Each PostgreSQL principal lifecycle job | 65 | 27 | 0 |

Root coverage is 90.52%, above the unchanged 90% requirement. Each principal
also passes all 96 selected CTK checks. Each lifecycle job contains 46 actual
native lifecycle nodes plus 19 other passing checks: the 65 total is not
misreported as 65 native lifecycle cases. The independent exact-source inspection
verifies all 184 mandatory new nodes, 46 on each principal/backend combination,
from verbose node identifiers and canonical Git source hashes. SQLite lifecycle
jobs also contain 119 declared foreign-backend/profile skips; those skips do not
qualify unavailable backends. The previously qualified 44 child and 42 scheduling
PostgreSQL cases remain green per principal. PostgreSQL's original principal
profiles pass 353 ADK and 387 LangGraph checks; optional Framework passes 183
(seven skips), and ten native saver checks pass (eight skips).

All five Windows profiles pass, including native lifecycle/receiver recovery.
Both main and both PostgreSQL runtime images pass their capability checks. All
seven complete dependency reports contain zero advisories. Both benchmarks prove
actual native checkpoint growth and one immutable interruption/resume action.
Each SDK's CTK artifact matches all 26 scenario hashes from the exact Git blobs;
the tested PR merge `95fb2aeb61ada96143349e89c2a151b5aa843f68` has this feature
head as a parent. Local CRLF bytes are not substituted for canonical sources.
Logs, artifact digests, mandatory native node lists and the read-only inspection
are retained under `tmp/ci-96ce610-*`; both tracking records preserve the complete
qualification record. Earlier nonzero/interrupted local batches remain historical,
as described below. S2-09 retains migration and broader recovery diagnostics;
Stage 4 retains independent JavaScript and complete proposed criteria.

## Earlier integrated history

The first integrated source `dc0af64` passed formatting, lint, strict typing
(86 runtime and three CI modules) and all five builds. Its four full test runs
were stopped after 113 seconds on early reusable-task control regressions;
all 372 input hashes remained unchanged and all four results are nonzero.
The logs and frozen manifest are retained in `tmp/s2-08-first-batch`.
Subsequent focused checks identify typed occurrence tuples passed to strict JSON
validation at new operation-key/trusted-admission serialization seams. The
correction uses JSON arrays at those seams, preserving immutable internal
identities and strict arbitrary-request validation. Required active-child and
native corrupted-receiver proofs are being added before final batch testing.
No interrupted run or static check qualifies a behavioral requirement.

## Integrated local verification — 2026-10-13

All 373 inputs of runtime source `688563a` remain unchanged throughout the
completed four-environment full batch; no supervisor reaches its fifty-minute
limit. Root coverage is 90.52%, above the unchanged 90% requirement.

| Full snapshot | Passed | Failed fixtures | Declared skips | Tracked expected failures |
| --- | ---: | ---: | ---: | ---: |
| Root | 2,414 | 1 | 1,423 | 2 |
| Actual ADK, shared contracts and CTK | 1,355 | 10 | 998 | 2 |
| Actual LangGraph, shared contracts and CTK | 1,394 | 10 | 971 | 2 |
| Optional Framework, shared contracts and CTK | 1,698 | 0 | 1,396 | 2 |

The principal failures are the observation wrapper not forwarding the new
private deadline state argument (four cases each), and the process-stop fixture
indexing an optional successful-result error field (six cases each). The workers
recover successfully. The root legacy-hash fixture built its expected old ABI
from an evolving dataclass, inadvertently adding the absent optional external
proof. Its expectation now states the fixed original field set explicitly.
Original budget, value/null/fault, identity, owner, zero-replay, commit-count and
legacy-hash assertions remain. The corrected fixtures and CI layout are the
only changes after the broad snapshot; every runtime hash is unchanged.

Corrected root legacy/CI focus passes 22 cases, with nine unavailable PostgreSQL
skips. Both actual principal engines pass all 54 selected SQLite cases in the complete
new lifecycle/key/receiver/compensation/corruption/process-stop block and existing
deadline fixtures under a 900-second supervisor (ADK 283 seconds, LangGraph
243 seconds). No test is skipped and all runtime/input hashes remain unchanged
during these corrected runs. Final results are retained in
`tmp/s2-08-final-focus-*`; final source still requires full hosted qualification.
All five fresh builds, documentation links, formatting, lint and strict typing
(86 runtime and three CI modules) pass. Earlier nonzero broad results remain
retained; they are not relabelled as successful full runs.

CI runs the complete new block in two additional independent native jobs while
the original suites retain their prior selection. Matching ignore/include
guards ensure every moved fixture is tested on both principal engines. Actual
PostgreSQL has two separate lifecycle profiles that also gate both images, and
Windows has its own lifecycle profile. Existing eight-minute command and
fifteen/twenty-minute job bounds remain; no required fixture is skipped to pass.

## Contract and limits

Operator cancellation is a durable business control. Its common terminal
snapshot, completion time and execution-lease revocation share one transaction.
A completed or faulted result retains its original outcome. Workflow-owned child
cancellation additionally requires the original parent identity and current
execution lease; a stale worker cannot cancel a child after takeover.

A deadline that interrupts an unresolved remote action retains the existing
recovery-required error and original deadline instead of fabricating a known
HTTP fault or allowing a retry. The child scope is cancelled explicitly; the
receiver's already committed business action and pending common receipt remain
visible. Ordinary deadlines with no uncertain external action retain their
existing timeout policy.

Host drain and lost ownership interrupt execution without inventing a business
cancellation or a known external outcome. Deadline, END and competitive-fork
controls retain their explicit scope and occurrence. Detached children preserve
their independent lifetime. Cooperative in-process interruption does not imply
hard preemption of blocking Python or undo of a committed remote action.

HTTP operation keys identify logical operations. Repeated loop, jump, reusable
task and fork occurrences receive distinct keys; retries and admitted recovery
retain the original key. Explicit prepared-request keys and already recorded legacy
keys remain unchanged. A receiver must enforce its own deduplication contract.

Pending external actions fail closed by default. A deployment may supply exact
trusted HTTP receiver declarations through the common service composition API.
The optional helper uses the business operation's bounded, authenticated,
TLS-verifying HTTP client with redirects disabled. Its finite profile admits
JSON-body POST/PUT/PATCH/DELETE operations on declared exact routes, pins the
request, input, principal, credential references and receiver contract version,
and queries the declared GET outcome route. Recovery does not repeat the business
request. Unknown or pending outcomes remain unresolved. Immutable receiver proof
must match the original operation key and independently recorded wire request;
missing or changed common proof rejects native recovery before dispatch.

Compensation uses an explicit standard workflow call to the declared inverse
operation, with its own receiver idempotency contract. A failed or uncertain
inverse remains explicit. Cancellation never creates an automatic compensation
stack or promises remote rollback. The official Open Workflow 1.0.3 schema and
private native checkpoint boundaries remain unchanged.

## Required finite evidence

| ID | Required outcome |
| --- | --- |
| LC01 | Atomic operator cancellation, terminal timestamp and revocation; active/waiting/reopened cases; late results cannot replace terminal control. |
| LC02 | Graceful drain and actual process stop recover permitted native/common state; ownership loss does not cancel business children. |
| LC03 | END, deadlines and fork winners cancel only verified synchronous scopes; detached lifetime and committed remote effects remain visible. |
| LC04 | Distinct logical keys and stable retry/recovery keys; request/receiver mismatch rejects before another business commit. |
| LC05 | Actual producer termination at dispatch/receiver-commit/receipt/native boundaries; retained value/null/fault and one receiver business commit; unresolved default remains fail closed. |
| LC06 | Explicit inverse operation executes once logically; compensation failure and uncertainty stay visible without an automatic rollback claim. |
| LC07 | Changed or missing intent/input/key/trust/principal/profile/native/index evidence rejects; ordinary records contain references rather than secrets. |
| LC08 | Identical actual ADK/LangGraph fixtures, SQLite/PostgreSQL, bounded optional Framework profile, root quality/coverage/build/schema and hosted Windows/image/security gates pass. |

All LC01-LC08 requirements are verified for the finite profile at `96ce610` by
the exact-source hosted evidence above. The selected fixtures do not establish
unsupported protocol, backend, automatic compensation or JavaScript capabilities.
