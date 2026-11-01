# Native state replay (opt-in source profile)

The opt-in profile `langgraph.state.replay@1.0.0` replays a completed-task prefix of a
workflow's root scope into a **new** invocation with immutable lineage to the source.
It uses the native checkpoint store, the common execution lease and the common effect
gate. SQLite is the embedded default; PostgreSQL is optional. This is a source profile,
not a published image, and S4-02 remains open. See the
[design record](s4-02-public-replay-admission-design.md) for what is deliberately not
done yet.

## Try it with the configured example

Mount the [bootstrap configuration](../examples/native-state-replay.yaml) and the
[OWS workflow](../examples/native-state-replay-workflow.yaml) under `/config`, provide
`OWA_NATIVE_STATE_TOKEN` through deployment secrets and retain `/data`. The example uses
the fake model, so it makes no external model call. Invoke it with `POST /v1/invoke` and
`{"input": {}}`, read its history, and replay the checkpoint after `first` as shown
below. The example declares `reexecute`, so the replay runs the second task again and
creates new receipts; change `replayEffectPolicy` to `read_only` to see the continuation
refuse its first effect instead. The policy must permit `catalog.call` and
`model.complete` for the agent call, plus `state.inspect` and `state.replay`.

## Enable it

Both `native_state: {}` and `native_state_replay: {}` are explicit opt-ins, and authenticated
API ingress with original execution authorization is required, exactly as for native state
views. Replay does not require `native_state_updates`. The block bounds native work
(`bounds`), the number of concurrently admitting replays (`max_active_replays`, default 2)
and the number of replays of one source invocation (`max_replays_per_source`, default 8). Default installations and ADK reject workflows that require replay.

The workflow declares the profile and its effect policy; infrastructure never decides it:

```yaml
document:
  metadata:
    owa:
      nativeExtensions:
        - {name: langgraph.state.read, version: 1.0.0}
        - {name: langgraph.state.replay, version: 1.0.0}
      nativeExtensionData:
        langgraph.state.replay@1.0.0:
          authorizationPolicyRef: <execution authorization id>
          replayEffectPolicy: read_only   # or reexecute
```

Grant the action `state.replay` in the execution authorization policy.

## Replay a checkpoint

Pick a checkpoint from the original invocation's history (`GET /v1/invocations/{id}/states`,
an item whose `root_task_visits` lists the tasks completed so far), then:

```http
POST /v1/invocations/{invocation_id}/state-replays
Authorization: Bearer <deployment token>
Idempotency-Key: replay-1
Content-Type: application/json

{"reference": "<opaque state reference>", "acknowledge_reexecution": false}
```

`acknowledge_reexecution` must equal whether the declared policy is `reexecute`; a
mismatch is rejected with `409` before anything is created. A read-only replay can never
execute an effect (the continuation faults at the first one), and a re-executing replay
creates new receipts. The response is `202` with only
`invocation_id` (the new invocation), `source_invocation_id`, `effect_policy` and
`state: accepted`; the new invocation continues in the background and is observed through
its own state and status. It never contains graph, thread or checkpoint identifiers,
receipts or the plan. The same key and request return the same target without repeating
work, including concurrent requests; a different request under the same key is rejected.
The source invocation and its authority are untouched.

## Authorization and limits

Only the original owner may replay, and both the current caller and the retained
original authority must permit `state.replay` on the invocation. Query parameters,
extra body fields and malformed references are rejected. At most `max_active_replays`
replays admit concurrently and at most `max_replays_per_source` replays may exist for one
source (`429` beyond either). The per-source limit is enforced inside the reservation
transaction that every replay serializes on, so concurrent distinct requests cannot
exceed it; a repeat of an admitted request is never a new admission and is never limited.
A replay target cannot itself be replayed (`409`, checked before any preflight work).
The state history of a replay target is also not currently readable through the state
views, so no reference to one can be obtained.

## Not implemented

Branching (data attribution), nested scopes, timing, declared channels and Command/Send workflows, replay from non-root
checkpoints, time travel and JavaScript parity are not implemented. Frozen-source
qualification is still required.

## Lifecycle of the new invocation

The continued target is an ordinary invocation owned by the same principal, so the common
lifecycle controls apply to it. A running replay target obeys the public cancel route: it
ends `cancelled`, performs no work after the in-flight call returns, and repeating the
replay request returns the same target without resurrecting it. Tests hold a re-executed
model call open, cancel through `POST /v1/invocations/{id}/cancel`, then release the call
(SQLite and PostgreSQL). Process stops are covered above.

A graceful engine shutdown mid-continuation leaves the target `running` (never terminal),
unregisters the replay controller and runs no further work. A restarted runtime on the same
database recovers it through the identical request, and because the interrupted model call
may already have happened it ends `faulted` without repeating the call. Changing the
execution policy invalidates the authority the source ran under, so a replay request made
afterwards is refused with a client error before any invocation is created or any model call
runs. Revocation *during* an already-running replay is not separately tested.

## Crash and restart

A process stop at any stage is recovered by repeating the identical request. The tests
start the real app in a subprocess, stop it after reservation, admission, native fork,
lineage binding, or inside the re-executed model call, wait for its execution lease to
lapse, and repeat the same `Idempotency-Key` request against a fresh runtime. The repeat
returns the same new invocation; a read-only replay or an interrupted effect ends faulted,
and no model call is repeated, so an effect that may already have happened is never
performed twice. This is qualified on SQLite and PostgreSQL (see the
[lineage acceptance record](s4-02-native-lineage-acceptance.md)); a client whose first
request never received a response must retry with the same key.
