# S2-03 recovery acceptance

The runtime baseline is `e39a7c042bd16e92fe760675855504afbeb510c3`; the process-stop fixture correction is
`d2d5934e9e38c666ef76172e3a9fc14db5c6e60c`. Final acceptance is
`ddd194ec990b1d0239cec3eaa88f2fa50d874bf7`. This audit checks the scoped
task against [Project Definition.md](../Project%20Definition.md), sections
10–12 and 26, and repository constraints.

Current decision: **verified**. All seven configured runs succeed: push CI
`38186556635`, PR CI `38186559193`, PostgreSQL `38186559187`, optional
Agent Framework `38186559206`, security `38186559195`, external sandbox
`38186559202` and docs `38186559212`. Both push and PR Windows suites,
native benchmarks and image stop/restart/resume steps pass. PostgreSQL
terminal logs prove the required fixture selections and atomic rollback test
actually execute. All six dependency reports have no nested advisory entries.

Historical artifact `29c681d` remains incomplete: its push Windows job was
cancelled after two process-boundary failures. The follow-up verifies the
committed expected terminal outcome, uses a five-second test lease and a
bounded twenty-minute Windows job; production ownership is unchanged.
The corrected eight process-stop cases pass locally per principal SDK and in
current configured suites. This completion does not close later backlog work
or promote full proposed/JavaScript criteria.

| Requirement | Implementation and behavioral evidence | Qualification |
| --- | --- | --- |
| Common public invocation identity and fingerprint; reject another engine or changed definition | [Common engine](../core/src/open_workflow_agent/engine.py), [invocation storage](../core/src/open_workflow_agent/persistence.py); [recovery admission](../tests/contract/test_recovery_admission.py) and [execution ownership](../tests/contract/test_runtime_execution_ownership.py) exercise rejection before dispatch or invocation mutation. | Verified at `ddd194e`; current local and configured evidence pass. |
| Original workflow input remains distinct from resume input; preserve JSON null, false, zero and empty values | [Recovery fixtures](../tests/contract/test_checkpoint_recovery.py) reopen services/engines and check original input, resume value, context and duplicate terminal resume. Shared-session cases in recovery admission preserve each invocation's input independently. | Verified at `ddd194e`; current local and configured evidence pass. |
| Terminal common outcome survives interruption before adapter finalization | Recovery fixtures cover explicit null and known completed/faulted outcomes without another action; [forced process-stop fixtures](../tests/contract/test_process_crash_recovery.py) kill the actual worker at terminal and receipt boundaries. | Verified at `ddd194e`; current local and configured evidence pass. |
| Native formats/checkpoints stay owned by their adapters; inconsistent state fails admission | [LangGraph state corruption](../tests/contract/test_native_state_consistency.py), [ADK input-event consistency](../tests/contract/test_adk_native_event_consistency.py), [completed scope dependencies](../tests/contract/test_native_scope_dependencies.py) and [native receipt dependencies](../tests/contract/test_native_receipt_consistency.py) mutate actual SDK storage. Checkpoint formats stay private and adapter-owned; the internal execution plan is not exposed. | Verified at `ddd194e`; current local and configured evidence pass. |
| Common intent/outcome records agree with native progress; known value/null/fault outcomes do not dispatch twice | [Leaf-action recovery](../tests/contract/test_effect_recovery.py), [sandbox recovery](../tests/contract/test_sandbox_effect_recovery.py), [event/child recovery](../tests/contract/test_event_child_effect_recovery.py) and [waiting-fork receipts](../tests/contract/test_native_receipt_fork_consistency.py) verify call counts, original outcomes, changed requests and uncertain intents. | Verified at `ddd194e`; current local and configured evidence pass. |
| Child terminal records/input and admitted descendant actions reconcile before parent state changes, including before a native task checkpoint | [Child dependency fixture](../tests/contract/test_child_invocation_dependencies.py) verifies clean value/null/fault results and rejects deleted/changed records, input, execution references, descendant receipts and index metadata with unchanged parent payload and one dispatch. [Common effects](../core/src/open_workflow_agent/effects.py) own the identity index and bindings. | Verified at `ddd194e`; current local and configured evidence pass. |
| Index and intent admission are atomic; previous non-child receipt digests remain valid | [Journal tests](../tests/core/test_effect_journal.py) inject a failed second write and assert both records roll back, check scoped reconciliation, and compare the prior receipt digest format. The PostgreSQL rollback case is in the actual configured store command. | Verified at `ddd194e`; current local and configured evidence pass. |
| Expiring ownership prevents concurrent/stale admission, common writes and native checkpoint commits | [Runtime ownership](../tests/contract/test_runtime_execution_ownership.py), [action authority](../tests/contract/test_action_execution_authority.py), [native fencing](../tests/contract/test_native_checkpoint_fencing.py) and process-stop cases test expiry, takeover, cancellation and resource draining. Container acceptance uses production lease expiry. | Verified at `ddd194e`; current local and configured evidence pass. |
| Principal-engine portability uses identical deterministic fixtures on both common backends | ADK and LangGraph execute the same shared fixtures with the same expected results. [Selection guards](../tests/ci/test_postgres_recovery_selection.py) inspect the actual CI expressions through pytest collection: nine scope, eighteen sandbox, twenty event/child and seventeen child-dependency cases per principal adapter. | Verified at `ddd194e`; current local and configured evidence pass. |
| Current source passes quality, build and deployment regression checks | Frozen full suites: root 1,664/621, ADK 918/494, LangGraph 960/467, optional Agent Framework 1,208/703, each with three tracked expected failures; root coverage 91.67%. Formatting, lint, strict 66-source and fixture/CI typing, five wheel/sdist builds, current Python/pinned-schema wheel bytes, audit integrity and documentation links pass. | Verified at `ddd194e`; current local and configured evidence pass. |

## Ownership of remaining feature work

The feature tracker deliberately maps some features to several tasks. S2-03
owns native checkpoint recovery and reconciliation of common invocation/action
records. Its completion does not claim the entire overlapping feature is done.

- Retry predicates, backoff/jitter, deadlines, timeouts and exception handling:
  **S2-04**.
- Embedded durable event inbox/outbox and timers: **S2-05/S2-06**.
- Recursive/asynchronous child scheduling, identity budgets and cancellation:
  **S2-07**, with broader lifecycle/compensation and ambiguous-outcome resolution
  in **S2-08**.
- Workflow-version and legacy-record migration: **S2-09**. Old unbound child
  receipts fail closed; existing non-child formats remain compatible.
- Public native history/replay/updates, native metadata extensions and full
  proposed/JavaScript criteria: **S4-01 through S4-05**.
- Optional Agent Framework native durability/hardening: **S4-09**. Its common
  bridge tests are supplementary evidence, not production-image qualification.

Pending effects retain truthful uncertainty and are not automatically
redispatched. Digests establish consistency, not authentication or exactly-once
effects. Sandbox policy/isolation and deployment capabilities remain unchanged;
new receipt fakes do not establish stronger isolation or real-cluster acceptance.
The official Open Workflow 1.0.3 schema is unchanged and core imports no SDK.

Source-bound historical results, including failures and previously deselected
cases, remain in [PROJECT.md](../PROJECT.md) and the
[implementation audit](../resources/compatibility/implementation-audit.json).
