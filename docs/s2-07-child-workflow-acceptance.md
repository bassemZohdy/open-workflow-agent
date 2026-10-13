# S2-07 child-workflow acceptance

Status: implementation in progress under the user-requested batch delivery.
S2-06 acceptance and these requirements remain open until final validation.
No feature or task is complete from this record.

Working changes now include an atomic child identity/input reservation and saved
root depth/total-child quotas. Siblings consume the same root budget, and policy
changes after reopen do not reset an existing tree's saved limits. Catalog
reuse pins the original child definition and preserves the canonical parent
task reference separately from the internal execution occurrence.

Detached execution has an atomic durable job/parent-return decision. The
parent returns transformed task input; the child retains an independent
lifetime and separate common identity. A bounded dispatcher uses the existing
engine ownership and native admission paths, and leaves armed child timers to
their exact original timer delivery. This source is unqualified until final
behavioral/process/database checks; formatting and type checks are preparation
evidence only. Quota/corruption and detached lifecycle/reopen fixtures are
staged for that final run. Synchronous nonresident child waits, durable
cancellation and complete process/corruption coverage remain open.

Authority: Project Definition.md, TODO S2-07, inventory A034-A036 and T09-T11.
The unchanged schema admits run.workflow and run.await. The pinned
[OWS reference](https://github.com/open-workflow-specification/specification/blob/9b5b1da29e9d4fff2358580241e11aab22704a16/dsl-reference.md#run)
requires asynchronous run to return transformed input without waiting for or
inventing the child result. Bootstrap policies and durable common metadata
must preserve separate identities; engine checkpoints stay private.

| Requirement | Required proof |
| --- | --- |
| CH01 | Explicit child version and resolved fingerprint remain pinned across parent recovery and latest catalog changes. Distinct invocation/session, canonical parent task reference, user correlation and authenticated owner are preserved. |
| CH02 | Nested synchronous output, null, fault, END and exported/context data preserve parent continuation without leaking private child context. Existing terminal dependency corruption tests remain enabled. |
| CH03 | Self-recursion with n=3 creates exactly four invocation identities and the base-case result. Mutual recursion terminates or reaches an explicit common policy error. |
| CH04 | Deployment depth and total-child-work quotas are enforced before new execution, across siblings, recursion and reopen. Repeated recovery cannot reset or spend the same reservation twice. |
| CH05 | Waiting synchronous children release parent execution without reporting a child fault. Original deadlines and parent/child cancellation remain effective during suspension and after process stop. |
| CH06 | run.await=false commits a durable child identity/input/definition binding before returning transformed task input. Child output is never fabricated into parent output. |
| CH07 | Detached work continues after successful parent completion and worker restart. A native task/promise alone is not durable dispatch. Parent cancellation and shutdown behavior follow explicitly documented lifetime policy, with no unnoticed orphan execution. |
| CH08 | Atomic child reservation, dispatch and terminal observation survive crashes before/after each boundary. Two workers and stale generations cannot create duplicates or replay known side effects. SQLite and actual PostgreSQL are required. |
| CH09 | Missing/changed bindings, input, identity, parent, budgets, native checkpoint or terminal dependency fail closed before model/action dispatch and before mutation of known state. |
| CH10 | Relevant shared, real ADK/LangGraph and process-stop tests, optional AF bounded behavior, quality/build/schema, configured PostgreSQL/Windows/images/security and exact capability limits pass before task closure. |

Current reviewed source e56e5b8 has separate synchronous common child identity,
terminal child dependency receipts, immutable local catalog versions and known
outcome corruption guards. It explicitly rejects background run at admission.
Finite self-recursion already works, but no recursive depth/total-child policy exists. Synchronous run waits on the child
invocation and treats nonterminal status as a fault, so nonresident child wait
needs dedicated integration. This is an actionable gap, not a deferred blocker.

Native baseline on unchanged source: LangGraph existing child dependency,
event/child effect recovery and catalog tests: 88 passed, 74 locally unavailable
profile skips, 43.96 seconds. Independent actual ADK passes the same 88 cases, with 74 locally unavailable profile skips, in 56.75 seconds. These existing
tests do not prove new CH01-CH10 behavior or actual PostgreSQL locally.

Broader cross-version migration belongs to S2-09; general compensation and
ambiguous external-action resolution to S2-08; general background protocol
adapters to S3-03; full proposed/JS qualification remains Stage 4. Preserve
those owners and default one-container embedded operation.

An untracked finite countdown probe passes on each actual principal SDK: n=3 produces four distinct invocation/session identities, the n=0 result, original principal/user correlation and pinned fingerprint, canonical parent reference and zero model calls. Each environment runs two adapter cases; its own SDK case is native, the other is an unavailable-SDK fallback. This establishes the finite recursion baseline only, without qualifying quotas or detached execution.
