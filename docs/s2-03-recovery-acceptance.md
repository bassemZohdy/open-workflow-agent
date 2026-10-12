# S2-03 recovery acceptance

The implementation baseline is `e39a7c042bd16e92fe760675855504afbeb510c3`.
Its acceptance artifact is `29c681d6f9759d70c93b7a20e99e2313a82102f7`.
This audit checks the scoped task in [TODO.md](../TODO.md) against
[Project Definition.md](../Project%20Definition.md), sections 10–12 and 26,
and the repository constraints. It does not close the remaining backlog or
promote the full proposed criteria in the feature tracker.

Current decision: **pending Windows push follow-up**. PR CI `38185162994`
succeeds, as do the other five configured runs. Push CI `38185159483` is
cancelled at its ten-minute Windows job limit; its log records two process-stop
failures before cancellation (terminal-null ownership loss, terminal-fault
marker with a still-running record). The push run is not qualified as successful,
despite its other jobs passing. PostgreSQL run `38185163003` passes 119 store checks, 10 native
LangGraph checks (eight skips), 223 shared LangGraph, 189 shared ADK and 147
optional bridge checks (seven skips), plus both runtime images. Logs confirm all
seventeen child-dependency cases per adapter and the PostgreSQL atomic rollback
case actually execute and pass. Security, docs, external sandbox and optional
Agent Framework jobs also succeed for the current artifact.

The follow-up requires the worker's terminal marker to observe the expected
committed outcome. Its shortened test lease is five seconds rather than two,
so cold synchronous SDK/jq work on a busy runner can reach the intended boundary;
the runtime's production lease is unchanged. The Windows job remains bounded at
twenty minutes to accommodate its expanded root/native suite. Local process-stop
checks pass eight cases per principal SDK, with 24 dependency/PostgreSQL skips
per environment. Fresh configured validation of this fixture change is required.

| Requirement | Implementation and behavioral evidence | Qualification |
| --- | --- | --- |
| Common public invocation identity and fingerprint; reject another engine or changed definition | [Common engine](../core/src/open_workflow_agent/engine.py), [invocation storage](../core/src/open_workflow_agent/persistence.py); [recovery admission](../tests/contract/test_recovery_admission.py) and [execution ownership](../tests/contract/test_runtime_execution_ownership.py) exercise rejection before dispatch or invocation mutation. | Current local root/native suites pass; current configured jobs pending. |
| Original workflow input remains distinct from resume input; preserve JSON null, false, zero and empty values | [Recovery fixtures](../tests/contract/test_checkpoint_recovery.py) reopen services/engines and check original input, resume value, context and duplicate terminal resume. Shared-session cases in recovery admission preserve each invocation's input independently. | Identical SQLite/PostgreSQL cases; local SQLite passes, configured PostgreSQL passes. |
| Terminal common outcome survives interruption before adapter finalization | Recovery fixtures cover explicit null and known completed/faulted outcomes without another action; [forced process-stop fixtures](../tests/contract/test_process_crash_recovery.py) kill the actual worker at terminal and receipt boundaries. | Local forced-stop cases pass; current PostgreSQL and image restart acceptance pending. |
| Native formats/checkpoints stay owned by their adapters; inconsistent state fails admission | [LangGraph state corruption](../tests/contract/test_native_state_consistency.py), [ADK input-event consistency](../tests/contract/test_adk_native_event_consistency.py), [completed scope dependencies](../tests/contract/test_native_scope_dependencies.py) and [native receipt dependencies](../tests/contract/test_native_receipt_consistency.py) mutate actual SDK storage. Public native identifiers and an execution plan are not exposed. | Both independent SDK suites pass; configured corrupt-state cases pass. |
| Common intent/outcome records agree with native progress; known value/null/fault outcomes do not dispatch twice | [Leaf-action recovery](../tests/contract/test_effect_recovery.py), [sandbox recovery](../tests/contract/test_sandbox_effect_recovery.py), [event/child recovery](../tests/contract/test_event_child_effect_recovery.py) and [waiting-fork receipts](../tests/contract/test_native_receipt_fork_consistency.py) verify call counts, original outcomes, changed requests and uncertain intents. | Current local suites pass; current configured cases pass. Event acknowledgements are not durable delivery. |
| Child terminal records/input and admitted descendant actions reconcile before parent state changes, including before a native task checkpoint | [Child dependency fixture](../tests/contract/test_child_invocation_dependencies.py) verifies clean value/null/fault results and rejects deleted/changed records, input, execution references, descendant receipts and index metadata with unchanged parent payload and one dispatch. [Common effects](../core/src/open_workflow_agent/effects.py) own the identity index and bindings. | Focus passes 77/74 in each principal environment. All seventeen configured PostgreSQL cases per adapter execute and pass. |
| Index and intent admission are atomic; previous non-child receipt digests remain valid | [Journal tests](../tests/core/test_effect_journal.py) inject a failed second write and assert both records roll back, check scoped reconciliation, and compare the prior receipt digest format. The PostgreSQL rollback case is in the actual configured store command. | SQLite, compatibility and actual configured PostgreSQL rollback checks pass. Older unbound child receipts require explicit recovery. |
| Expiring ownership prevents concurrent/stale admission, common writes and native checkpoint commits | [Runtime ownership](../tests/contract/test_runtime_execution_ownership.py), [action authority](../tests/contract/test_action_execution_authority.py), [native fencing](../tests/contract/test_native_checkpoint_fencing.py) and process-stop cases test expiry, takeover, cancellation and resource draining. Container acceptance uses production lease expiry. | Local suites pass; configured ownership/fencing passes; final main CI image checks pending. |
| Principal-engine portability uses identical deterministic fixtures on both common backends | ADK and LangGraph execute the same shared fixtures with the same expected results. [Selection guards](../tests/ci/test_postgres_recovery_selection.py) inspect the actual CI expressions through pytest collection: nine scope, eighteen sandbox, twenty event/child and seventeen child-dependency cases per principal adapter. | Seven collection guards pass. Native environments remain independent. Required PostgreSQL cases are confirmed in terminal logs. |
| Current source passes quality, build and deployment regression checks | Frozen full suites: root 1,664/621, ADK 918/494, LangGraph 960/467, optional Agent Framework 1,208/703, each with three tracked expected failures; root coverage 91.67%. Formatting, lint, strict 66-source and fixture/CI typing, five wheel/sdist builds, current Python/pinned-schema wheel bytes, audit integrity and documentation links pass. | Current Windows, both main CI image/benchmark steps, PostgreSQL images and six nested dependency reports remain to qualify. |

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
