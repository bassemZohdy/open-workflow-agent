# S4-02 public replay admission: design record

Status: proposal, 2026-11-01, against `main` at `7faf2e6`. The project owner approved
designing and implementing public replay admission as the next S4-02 piece; no code
changes accompany this record. Nothing here is advertised, qualified or shipped, and
S4-02 stays open. Choices marked **chosen** are defaults I picked so work can proceed;
each is listed for review at the end.

## What already exists

- A private root coordinator, `NativeReplayCoordinator.run(source, authority, request_id,
  source_reference, acknowledge_reexecution)` in the LangGraph adapter. It is restartable
  and idempotent across its stages (reserve, admit under the original authority, native
  fork, lineage binding, owned continuation) and returns the new invocation's result. It
  requires trusted execution authority and is explicitly not a public boundary.
- The supported profile is a contiguous completed-task prefix of the root scope. Timing,
  declared channels, Command/Send, nested scopes and state controls are excluded.
- Workflow declaration `langgraph.state.replay@1.0.0` with
  `{authorizationPolicyRef, replayEffectPolicy: read_only | reexecute}`, compiled into an
  immutable policy whose digest binds each request.
- A common lineage registry that remaps source receipts and a scoped gate that checks
  the effect policy at the common action boundary.
- The public pattern to follow is the state-update ingress: a thin route module
  (`state_control_api.py`), a core controller, a bootstrap block (`native_state_updates`),
  a capability entry, an `Idempotency-Key` header and original-owner authorization.

## Proposed contract

**Opt-in.** A bootstrap block `native_state_replay` (bounded work, response and
concurrency limits, validated like `NativeStateUpdateConfig`). It requires
`native_state` and authenticated API ingress with original execution authorization, as
native state views do. It does not require `native_state_updates`. Default
installations and ADK reject required replay workflows, as today.

**Route.** `POST /v1/invocations/{invocation_id}/state-replays` with body
`{"reference": "<64 hex state reference from /states>", "acknowledge_reexecution": bool}`
and one `Idempotency-Key` header. The request id is
`sha256(json([profile, invocation_id, key]))`, as the update route derives it. Query
parameters are rejected.

**Authorization.** Action `state.replay` on `invocation:{id}`, checked for the current
caller and again against the retained original authority, and only for the original
owner. The new invocation keeps the same owner and original authority. Authorization is
re-checked after the work, as the update route does.

**Effect policy.** The workflow declaration decides. A request whose
`acknowledge_reexecution` does not equal `replayEffectPolicy == reexecute` is rejected
with a common error before any state is created; read-only replays can never execute an
effect and re-execution creates new receipts.

**Identity and response.** A replay creates a new business invocation with immutable
lineage to the source; the source's history and authority are untouched. The response
carries the new `invocation_id`, the source id, the exact `request_digest`, the effect
policy and the status. It never contains graph, thread or checkpoint ids, receipts, or
the internal plan.

**Execution model (chosen).** The route admits and binds synchronously through the
idempotent stages, then continues the new invocation through the existing background
execution and resume machinery and answers `202` with the new invocation id; clients
poll the new invocation. This avoids holding an HTTP request open for an arbitrary
workflow and reuses the status, cancellation and recovery paths. It needs the
coordinator split at its "bound" phase; a repeat with the same key returns the same
target and its current status.

**Capability.** `features.nativeStateReplay` with profile, endpoint, scope `root`,
`completedTaskPrefix`, the effect policies, `branch: false` and `timeTravel: false`,
advertised only when configured and the engine advertises the extension.

## Lifecycle budgets (chosen defaults, to be bounded in configuration)

At most 2 concurrently admitting replays and 8 replay targets per source invocation, and
no replay of a replay in the first profile (lineage depth 1). These bound storage and
prevent fork storms; they are configuration limits, not new semantics.

## Acceptance gates before anything ships

1. HTTP-level tests on SQLite and PostgreSQL: non-owner, missing action and revoked or
   changed original authority are rejected; an identical repeat returns the same target;
   a changed request under the same key conflicts; an acknowledgement mismatch is
   rejected; read-only replays never run an effect; re-execution creates new receipts;
   the source is unchanged.
2. Crash and restart at each coordinator stage driven through the public route, plus
   concurrent same-key requests, response and concurrency budgets, and absence of native
   identifiers in every response and error.
3. Lifecycle gate installation for the continued target and the budgets above.
4. A configuration-only example and operator documentation, then frozen-source
   qualification on both platforms.

## Still out of scope

Branching (data attribution), nested scopes, replay of timing, channels or Command/Send
workflows, replay from non-root checkpoints, time travel, and any JavaScript parity.

## Choices to review

1. Asynchronous `202` with polling rather than a synchronous result.
2. The lifecycle budget defaults above and replay depth 1.
3. Replay not requiring `native_state_updates`.

## Implementation status (2026-11-01)

The opt-in profile is implemented behind `native_state_replay` as described in
[the operator guide](native-state-replay.md), using the three choices above as proposed
(`202` with polling, an active-replay budget, no `native_state_updates` requirement).
HTTP-level tests pass on SQLite and PostgreSQL for the capability entry, both effect
policies, idempotent and concurrent same-key requests, a changed request under a used
key, acknowledgement mismatch, other principals, malformed input and the active
budget.

The per-source limit (`max_replays_per_source`) and replay depth 1 are also enforced, the
former atomically inside the reservation transaction.

A configuration-only example (`examples/native-state-replay*.yaml`) boots the real app
and replays through public requests only, tested on SQLite and PostgreSQL.

Not yet done, so S4-02 stays open: crash and restart driven through the route,
lifecycle gate installation for the continued target, and frozen-source qualification on
both platforms. The remaining out-of-scope items above are
unchanged.
