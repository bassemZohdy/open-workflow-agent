# S2-08 lifecycle and external-action acceptance

Status: implementation in progress; S2-08 remains unchecked. No new lifecycle
or receiver-recovery capability is qualified by this record until the complete
batch passes its required native, storage, process-stop and hosted gates.

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

The combined batch will record exact source hashes, commands, counts, skips,
expected failures and artifact links here. A passing subset does not close the
task or establish unsupported protocol, backend or JavaScript capabilities.
