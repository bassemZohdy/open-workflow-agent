# Open decisions blocking the remaining backlog

Recorded 2026-11-01 after the consolidation merges (backlog 31/37). Every item below is
gated by a decision or permission that belongs to the project owner, not by further
implementation effort. Nothing here changes a contract or promotes a task; each entry
states what is needed, the options and a recommendation so the choice can be made once.

## 1. Node `jq` implementation (unblocks S4-03 expressions, `for`, `switch`)

The Node engine has no expression evaluator, and the proposed tests are roughly 85%
expression/data-flow dependent (see [feasibility](s4-04-proposed-test-feasibility.md)).
Core pins jq 1.8.2 exactly.

| Option | Effect |
| --- | --- |
| A. Add `jq-wasm@3.0.0-jq-1.8.2` pinned exactly, installed with `--ignore-scripts`, plus an audit and lock update | Same jq version as core, so shared fixtures can compare outputs directly. Adds about 4 MB of WebAssembly and a third-party dependency. |
| B. A different vetted jq binding | Needs its own version-parity proof against jq 1.8.2. |
| C. Keep Node literal-only | No new dependency; S4-03 and most of S4-04 stay blocked. |

Recommendation: A. The first attempt to install it was denied by the permission
classifier and was not retried; an explicit approval from the owner is required.

## 2. The "candidate OWS runtime" (blocks 420 proposed tests) — decided 2026-11-01

**Decision (project owner, 2026-11-01): narrow the stated backends.** The bridge records it
as `scope_decisions`, the tracker's original fields are preserved, and no row is credited
by it. The text below is the original brief.

Those rows name a backend that has no adapter, runner or acceptance record in this
repository. Either provide it, or record a decision to narrow the stated backends of
those rows. Recommendation: narrow them to the engines this repository can run, and
track the candidate runtime as a separate comparison task if it is still wanted.

## 3. S4-02 contracts that need an owner

- Additional native stream modes (`messages`, `tasks`, `debug`, custom) need explicit
  access and redaction contracts before implementation.
- Public replay/branch admission needs a decision on the public request shape,
  authorization action, lifecycle budgets and branch data attribution.
- Memory (T51/T68) beyond the named profile needs a decision on the supported
  provider/model matrix and its qualification environment (PostgreSQL with vector
  extension, local model).

Recommendation: decide streams and replay first; both reuse components already
qualified privately.

## 4. Conditional tasks S4-09, S4-10, S4-11

`TODO.md` activates each only on recorded requirements:

| Task | Needed before work starts |
| --- | --- |
| S4-09 Agent Framework hardening | A named user need, deployment target, compatibility promise and rollback owner ([plan](agent-framework-production-readiness.md)). |
| S4-10 External events | A real consumer and transport, plus delivery, security and operational contracts ([plan](event-integration-readiness.md)). |
| S4-11 Multi-tenancy / delegated consent | A concrete use case with owners and trust boundaries ([decisions](protocol-security-decisions.md)). |

Recommendation: leave all three dormant unless one of those requirements exists.

## 5. Stage 5 backlog branch

`origin/docs/backlog-runtime-decision` adds Stage 5 runtime-decision acceptance tasks
(S5-01 to S5-04) sourced from an external report. It conflicts with the current
`TODO.md`. Options: rebase and adopt it, adopt only the tasks that still apply, or
drop it. Recommendation: decide after item 2, since both depend on the candidate runtime.
