# S4-04 proposed-test mapping feasibility

Recorded 2026-11-01. This is analysis of the 443 proposed tests in
[the feature tracker](../resources/compatibility/feature-tracker.json), not evidence.
All 443 rows in [the coverage bridge](../resources/compatibility/coverage-bridge.json)
stay `unmapped`; no selector is credited, because the bridge requires a selector
for the row's complete stated backend scope.

## Backend scope as stated by the rows

| Stated backends | Rows |
| --- | ---: |
| OWA reference; LangGraph Python; LangGraph JS; candidate OWS runtime | 420 |
| LangGraph Python engine; LangGraph JS/TS engine; portable subset reference runtime where applicable | 23 |

Every row therefore requires the JavaScript engine, and 420 also require a
"candidate OWS runtime" that is not part of this repository and has no adapter,
fixture runner or acceptance record here. Those 420 cannot be fully executed from this
repository however good the Python and Node evidence is.

## What the fixtures need (keyword scan, heuristic)

A scan of each row's fixture and expected-outcome text for capability terms gives
overlapping counts; it overstates nothing it cannot find but misses rows that use
different words:

| Capability named | Rows |
| --- | ---: |
| Expressions, `jq` or data flow (`export`, `as`, `${ }`, schemas, secrets) | 377 |
| Events, timers, schedules or human tasks | 53 |
| Calls, agents, models or protocols | 44 |
| Retry, timeout or catch | 43 |
| Extensions, hooks or security capabilities | 38 |
| Crash, restart or durable state | 24 |
| External store or service | 7 |

The Node engine currently executes only literal `set`, `do`, `raise`, `try`/`catch`
and non-competing `fork`, with no expressions, calls, durability or public identity
(see [the S4-03 scope](s4-03-javascript-execution-acceptance.md)). It cannot run
the fixtures behind roughly 85% of the rows even before the candidate runtime is
considered.

## Rows that fit the literal profile, and why none is credited

- **T04 scoped transitions:** nested scopes and `exit`/`end` fit the shared fixtures
  already in `tests/fixtures`, but the criteria require "expected task histories", and
  the row names the candidate runtime.
- **T06 all-branch fork:** the ordered array output matches the shared fork fixtures,
  but the row varies branch delay order (needs `wait`), requires join isolation checks
  and names the candidate runtime.
- **T63 scoped end across child graphs:** scoped `exit`/`end` fit, but the criteria need
  reference traces, scope frames and cancellation links, and `end` with active sibling
  branches is outside the Node fork profile (a `then` on a branch task is rejected).

Shared-fixture overlap on these is useful regression evidence but is explicitly
insufficient for the bridge, so it is not credited.

## Scope decision (2026-11-01)

The project owner decided to narrow the stated backends of the 420 rows to the engines
this repository can run, so the candidate runtime is no longer required. This is
recorded in the bridge's `scope_decisions`; the tracker's original fields are preserved
and no row is credited. The JavaScript engine, expressions, durability and history/trace
criteria below still apply.

## What would unblock mapping

1. ~~A decision on the candidate OWS runtime~~ (made; see above).
2. Calls and durable state in the Node engine; `set` expressions now exist (see the
   [S4-03 scope](s4-03-javascript-execution-acceptance.md)), but `for`, `switch` and
   conditions do not.
3. Lifecycle-history and trace capture comparable across engines for the
   history-based criteria (T04, T63 and similar).
