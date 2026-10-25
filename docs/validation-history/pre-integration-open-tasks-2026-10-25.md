# Retained ready-task notes before main integration

These are historical unchecked task notes, preserved when the active backlog
was condensed for PR #64. Links are rebased for this location. Current scope
and status remain in [TODO.md](../../TODO.md) and
[PROJECT.md](../../PROJECT.md); no checkbox is promoted here.

## S4-02

```text
- [ ] **S4-02** (L) — Add declared native channels/reducers, Command/Send, memory, streams, state updates, replay and time travel.
  A later bounded native Send candidate dispatches non-competing forks through
  the actual SDK while core retains join/end/error/receipt policy. Thirteen
  focused cases include physical SQLite/PostgreSQL recovery; Linux/full-source
  and frozen default-fork regression qualification remain pending.
  A later fix restores the existing owned waiting/running/completed transitions
  when an ending branch wins after a sibling waits. Deterministic regressions
  cover both fork modes. The PostgreSQL-only native selection passes 30 cases;
  current-source qualification remains required. See the acceptance record for
  retained failures, skips and isolated reruns.
  A bounded required `langgraph.command@1.0.0` candidate now uses real SDK
  Command updates and per-scope transitions without a new public payload.
  Required/optional root/nested routes and ADK admission rejection pass locally.
  Nested-reader checkpoint privacy is repaired and forged stored bindings reject
  before mutation on SQLite/PostgreSQL. Four Command/default × root/nested
  SQLite process-stop cells pass. Full qualification and the remaining Send,
  memory, streams, state-update, replay and time-travel scope stay open.
  The first scoped channel/reducer slice is authored for the exact
  `langgraph.channels@1.0.0` extension using private reducer-backed LangGraph
  state. Its focused core (10 cases) and native channel (3 cases) tests pass.
  A LangGraph × SQLite/PostgreSQL process-stop regression now checks the
  committed reducer delta survives restart without duplication; the SQLite
  selection passes locally and the PostgreSQL selection awaits hosted CI.
  A `last_value` integration regression exercises exact extension admission,
  a writer task, and a later reader binding; its focused local selection passes.
  A native run/pause/resume regression checks channel values across a wait
  boundary and remains pending its engine-specific selection. The SQLite
  process-stop case passes locally; PostgreSQL and exact-head hosted
  qualification remain pending. Broader S4-02 features remain open. See
  [the S4-02 acceptance record](../s4-02-native-channels-acceptance.md).
```

## S4-03

```text
- [ ] **S4-03** (L) — Implement and independently qualify JavaScript/TypeScript; keep Python-only and JS-only differences visible.
  A later literal-fault candidate adds standard inline `raise.error` and four
  exact shared fault cases. Node passes 48 package tests and each actual Python
  SDK passes all 13 shared success/fault cases. A format-validation difference
  remains explicit; full current-source/Docker qualification is pending.
  An experimental native literal execution component now supports `set`, nested
  `do` and fixed scope transitions. Nine exact fixtures run on Node and Python;
  unsupported features reject before native graph construction. It has no public
  invocation identity, persistence, recovery, expressions or calls and is not a
  full engine adapter. Windows Node 24 passes 43 package tests; further actual
  SDK/Linux qualification and the broader task scope remain required.
  An optional pinned LangGraph/TypeScript scaffold and native smoke test now
  exist under `engines/langgraph-js`. Its loader validates YAML/JSON against the
  SHA-256-pinned official OWS 1.0.3 schema and sanitizes malformed-YAML errors;
  local Node tests and the production dependency audit pass. The loader now
  rejects source above 1 MiB measured as UTF-8 before parsing, with boundary
  and multibyte cases included in the consolidated local batch. A second loader slice now
  mirrors Python admission rejection for duplicate YAML/JSON keys, cyclic
  aliases, implicit timestamps and non-finite values; those cases were included
  in the consolidated local batch. Python and TypeScript now consume shared admission fixtures,
  including rejection of numeric/boolean YAML mapping keys and acceptance of
  equivalent string-keyed JSON, with error-category parity cases. The Node 24
  package suite (10 tests) and production dependency audit passed locally for
  the consolidated batch; exact-head hosted qualification remains pending. The
  package's wider OWS compilation/execution and execution-adapter parity remain
  unimplemented; complete those and independently qualify the engine before claiming full
  parity. Frozen predecessor sources remain
  reference-only.
```

## S4-04

```text
- [ ] **S4-04** (L) — Automate official CTK plus all proposed tests, schema constraints, parity and cross-engine outcome checks.
  A finite comparison candidate captures the 13 shared literal success/fault
  outcomes from actual ADK, LangGraph Python and Node, then requires matched
  source/SDK/runtime/fixture/schema evidence in CI. All 13 actual Windows native
  outcomes match at `7d1a37b`, with fixture bytes matching Git. Hosted/full-source
  qualification remains pending; no proposed/schema criterion is promoted.
  `resources/compatibility/coverage-bridge.json` now inventories all 443
  proposed test IDs and 774 schema-constraint rows. Every row remains explicitly
  unmapped and unexecuted because partial overlap is not evidence of equivalent
  coverage. An integrity test checks exhaustive IDs and requires live selectors
  and retained evidence before any row can be promoted. The bridge now includes
  reasoned blocker counts (443 tests, 774 constraints) guarded against row
  status drift. Integrity assertions also retain source verification guidance
  on all 774 schema rows; the AST selectors passed in the consolidated local
  suite. These checks do not count as schema coverage. Selector promotion rejects `skip`, `skipif`, and
  `xfail` markers, including module-level skip markers; its AST regression also
  passed in the consolidated local suite and does not establish criterion
  coverage. Full CTK automation and parity qualification remain open. See
  [the coverage bridge](../../resources/compatibility/README.md).
```
