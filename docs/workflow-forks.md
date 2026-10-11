# Ordered fork results

S2-01 corrects noncompetitive fork output to the pinned OWS 1.0.3 rule: an array
of branch outputs in declaration order. Completion order and output value type
do not change that shape. Object keys are retained in separate branch results,
and original input is not implicitly merged into the result.

```yaml
do:
  - parallel:
      fork:
        branches:
          - first:
              set: {shared: first}
          - second:
              set: {shared: second}
```

The output is `[{"shared":"first"},{"shared":"second"}]`, including when
`second` finishes first. Nested forks retain nested arrays; null, scalar, list
and object outputs retain their JSON types.

Branches receive separate input/context snapshots. Branch-local exports affect
that branch's subsequent tasks; they do not silently replace the parent's
context or a sibling's snapshot. Fork-level output/export uses the common data
pipeline and can explicitly aggregate results or replace parent context.

For example, an application that deliberately wants the earlier merged object
shape can request it through the standard output expression:

```yaml
output:
  as: '${ reduce .[] as $branch ($input; . + $branch) }'
```

That expression explicitly starts with transformed fork input and chooses
declaration-order object merging, including the desired duplicate-key behavior.
Native channel reducers are not inserted into standard OWS data flow. Declared
native channels/reducers remain S4-01/S4-02 work; complete proposed criteria and
independent JS qualification remain separate requirements.

Competitive fork output and whole-workflow end behavior retain their distinct
scopes. Durable winner selection/cancellation and native/common effect recovery
remain S2-02/S2-03/S2-08 work. Current local tests cover reversed completion,
JSON output types, nested arrays, branch export isolation and explicit
aggregation and sibling cleanup after branch failure; see
[the shared fixtures](../tests/contract/test_fork_results.py). A real SQLite
restart preserves the ordered array and does not replay completed branch calls;
its configured PostgreSQL counterpart passes in the database acceptance gate.
The shared fixture also reopens storage and resumes interrupted forks on every
adapter, checking the ordered output and persisted completed result. It does not
establish general pending-effect transactions or exactly-once external actions.
The [native primitive checks](../tests/langgraph/test_native_state_collisions.py)
verify default same-key collision rejection and explicit append aggregation
in the pinned SDK. They do not expose those channels as workflow metadata.
S2-01 acceptance is recorded in
[PROJECT.md](../PROJECT.md#s2-01-ordered-fork-results--2026-10-11-verified).

## Competitive fork integration under verification

The S2-02 implementation records the first completion observed by the executor.
The pinned specification defines one winning branch output; the backlog requires
first-completed selection. An observed branch fault is therefore a faulted
winner, and JSON null is a completed winner. There is no first-success fallback.
When branch completions are ready together, the first committed decision is
retained; declaration order does not replace it.

The private common decision records output or error, original task diagnostics,
workflow-end status and loser cancellation intents before cancellation. On
replay, the executor reads that decision before scheduling either branch.
Invocation, frozen workflow fingerprint, resolved call site and ancestor/attempt
occurrences identify separate races. Native checkpoint state stays private to
each engine. No public plan or workflow schema change is involved.

Loser tasks are cancelled and awaited, including cleanup that yields. A separate
receipt records draining. After process interruption, a saved winner survives
without restarting its losing branch. Cancellation and a cleanup receipt do not
undo a committed external action or prove distributed compensation. General
pending-action recovery remains S2-03/S2-08.

The [competitive fixtures](../tests/contract/test_competitive_forks.py) exercise
completion order, fault/null/end, cancellation before a winner, losing waits,
loop/reusable/nested/retry identity and fresh-service replay at cleanup
boundaries. Configured PostgreSQL fixtures are in its integration gate. S2-02
remains open until its remaining failure/restart cases and fresh acceptance
gates pass; see [current evidence](../PROJECT.md#s2-02-competitive-fork-decisions--in-progress).
