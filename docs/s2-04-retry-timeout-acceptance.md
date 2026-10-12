# S2-04 retry, timeout and exception acceptance

Decision: **local acceptance verified; configured qualification pending**.
The frozen implementation is `a0ee9c0b26823c256cefe0f39b6ccd6e17e4994a`.
The task in
[TODO.md](../TODO.md) covers retry predicates/backoff/jitter, task/workflow
timeouts, original deadlines and exception handling. The current source also
preserves those policy decisions across restart, using common immutable receipts
and adapter-owned native checkpoint references. Completion requires every row
below and exact-source configured acceptance; focused passes do not close the task.

Full local suites pass: core 1,732/728 at 91.61% coverage, ADK 985/579,
LangGraph 1,027/552 and optional Agent Framework 1,277/820, each with three
tracked expected failures. Six knowledge-extra checks separately pass. Strict
typing, scoped lint/formatting, all five wheel/sdist builds and frozen source/
pinned-schema wheel bytes pass. Prior artifact `fdd771e` has three successful
runs and four fixture failures; it is not all-seven success. The external-intent
count and imported in-flight timeout fixtures are corrected in current source.

Test-only correction `b7d8674` follows a Windows descriptor/data-flow failure
at `3fb1041`: a one-second success-path budget expires during SDK/storage work.
The fixture now uses a thirty-second bound; resolved-descriptor assertions are
retained. Actual timeout controls and production source are unchanged. Root,
ADK and LangGraph catalog suites each pass 41 tests; the entire local Windows
root check passes 460/12. Fresh configured qualification remains required.

| Requirement | Behavioral evidence | Current qualification |
| --- | --- | --- |
| Declared catch error variable is available before filtering and predicates | [Error context](../tests/contract/test_retry_error_context.py) checks default/custom aliases, declared errors, built-in status, when/exceptWhen and retry predicates. | Prior correction qualified at `b6aef8c`; current regression pending. |
| Retry count bounds, including an absent count | [Recorded clock/RNG fixtures](../tests/contract/test_retry_timing.py) assert actual call counts, bounded additional retries and explicit policies without a count. | Focus passes on both principal adapters; full/configured pending. |
| Constant, linear and exponential delays and bounded jitter | The same fixtures assert exact delays and draws. [Typed policy](../core/src/open_workflow_agent/retry.py) realizes constant base, linear base × retry ordinal, exponential base × 2^retry index, plus uniform additive jitter. [Core policy tests](../tests/core/test_retry_policy.py) reject invalid limits, reversed jitter and invalid draws. | Runtime timing qualified at `cbb06b2`; current full/configured pending. |
| Per-retry and aggregate duration budgets | Clock fixtures cover zero and positive limits; aggregate timing starts at the first selected retry and includes delays. A retry can only begin inside its remaining budget. | Current local focus passes; exact configured acceptance pending. |
| Original workflow/task deadlines survive recovery | [Deadline restart fixtures](../tests/contract/test_deadline_recovery.py) stop at an actual native wait, reopen services and adapters, then check expired/remaining budgets, unchanged anchor bytes and call counts. | Eight SQLite cases per principal pass; current PostgreSQL/full pending. |
| Retry clock, schedules, sampled jitter and attempt admission survive recovery | [Retry recovery fixtures](../tests/contract/test_retry_schedule_recovery.py) stop in a delay, before task checkpoint after a known success, or during an uncertain action; recovery checks remaining delay, no redraw, stable receipt bytes and no repeat dispatch. | Five SQLite cases per principal pass; current PostgreSQL/full pending. |
| Expired admitted retry can reconcile known outcomes without new work | Retry recovery preserves the known successful result after the aggregate budget expires. Core reconciliation permits recorded outcomes and pure data reconstruction; a new external action, missing policy decision or pending wait is rejected. Uncertain intents remain unresolved. | Actual principal adapter cases pass; full/configured pending. |
| Clocks and decisions are immutable, atomic and owned | [Decision storage tests](../tests/core/test_policy_decisions.py) inject a failed second write, assert rollback of index and outcome, check factory invocation counts and reject stale ownership. [Common journal](../core/src/open_workflow_agent/effects.py) records internal choices without a pending external intent. | SQLite passes; actual PostgreSQL atomic test required. |
| Inconsistent recovery evidence fails before parent mutation | Deadline/retry fixtures delete or change saved records and verify common admission failure, unchanged parent payload and no additional calls. Native adapters bind the same opaque receipt references in their own formats. | Actual SQLite SDK cases pass; configured/full pending. |
| Timer errors preserve uncertainty and drain resources | Clock fixtures stall an external action under task/workflow/per-attempt timeouts. Cancellation wrappers propagate shared uncertainty so catch/retry cannot dispatch again. Core tests distinguish foreign TimeoutError and account for synchronous elapsed work. | Both principal focused suites pass; current full/configured pending. |
| Structured OWS errors and lexical catch order | [Scoped exception fixtures](../tests/contract/test_scoped_exception_policies.py) check standard communication/expression/timeout types, status/instance filters, inner/outer order, handler failures and predicate failures. Declared error fields override the standard fallback; common API serialization retains code/message/details. | Actual principal fixtures pass; current full/configured pending. |
| Real process interruption preserves temporal decisions | [Forced-stop tests](../tests/contract/test_timing_process_recovery.py) kill the actual worker without cleanup, then recover in another process after real database lease expiry. Expired deadlines stop another call; retry recovery preserves one jitter draw and two total calls. | Three SQLite cases per principal pass; configured PostgreSQL/process regression pending. |
| Full source, quality, package and deployment regression | Four frozen full suites, coverage ≥90%, strict package/fixture typing, lint/formatting, five wheel/sdist builds and current source/pinned-schema wheel bytes; both principal native environments, Windows, PostgreSQL, image restarts, benchmarks and six nested dependency reports. | All frozen local suites/builds/quality pass; fresh configured acceptance required. |

Private policy records are not a copy of native execution state or the internal
plan. The official Open Workflow 1.0.3 schema and sandbox/deployment capability
gates remain unchanged. No paid service is used by these fixtures.

The timer model accounts for wall-clock deadlines and cooperative cancellation;
it does not promise hard preemption or stronger process isolation. Common known
action outcomes remain immutable. Pending external actions require explicit
resolution rather than an automatic retry.

Legacy nonterminal timed invocations without the required policy clock fail
closed. Compatible version/legacy migration remains **S2-09**. Durable event
inbox/outbox/timers, asynchronous child scheduling, compensation and broader
ambiguous-outcome resolution remain **S2-05 through S2-08**. Public native/JS
extensions, tracing, all proposed criteria and independent JavaScript acceptance
retain their **Stage 4** owners. The full user goal remains the complete backlog.
