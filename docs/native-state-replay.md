# Native state replay (opt-in source profile)

The opt-in profile `langgraph.state.replay@1.0.0` replays a completed-task prefix of a
workflow's root scope into a **new** invocation with immutable lineage to the source.
It uses the native checkpoint store, the common execution lease and the common effect
gate. SQLite is the embedded default; PostgreSQL is optional. This is a source profile,
not a published image, and S4-02 remains open. See the
[design record](s4-02-public-replay-admission-design.md) for what is deliberately not
done yet.

## Enable it

Both `native_state: {}` and `native_state_replay: {}` are explicit opt-ins, and authenticated
API ingress with original execution authorization is required, exactly as for native state
views. Replay does not require `native_state_updates`. The block bounds native work
(`bounds`) and the number of concurrently admitting replays (`max_active_replays`, default
2). Default installations and ADK reject workflows that require replay.

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
replays admit concurrently (`429` beyond that); a repeat of an admitted request is not a
new admission.

## Not implemented

Per-source replay limits and replay depth limits, branching (data attribution), nested
scopes, timing, declared channels and Command/Send workflows, replay from non-root
checkpoints, time travel and JavaScript parity are not implemented. Crash and restart are
qualified at the coordinator level (see the
[lineage acceptance record](s4-02-native-lineage-acceptance.md)) but not yet through this
route, and lifecycle gate installation, a configured example and frozen-source
qualification are still required.
