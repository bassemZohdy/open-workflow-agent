# S4-02 selected-checkpoint lineage preparation

Updated 2026-10-27. S4-02 and T49 remain open. This private component validates
the basis for a future native replay or branch. It does not create a child
invocation, fork native checkpoints, dispatch effects, expose an HTTP route or
advertise a replay/branch capability. Both principal engines reject required
replay/branch profiles before invocation creation.

## Declared policy and common command

The compiler accepts exact versioned metadata for `langgraph.state.replay@1.0.0`
or `langgraph.state.branch@1.0.0` only with its required declaration. Each policy
has an `authorizationPolicyRef` and its respective `replayEffectPolicy` or
`branchEffectPolicy`, exactly `read_only` or `reexecute`. Branching also requires
the existing bounded update policy with the same authorization reference.
Missing, optional, unknown-version, malformed or extra policy data is rejected.
The official OWS 1.0.3 schema is unchanged.

The immutable common request pins distinct source/target business identities,
workflow fingerprint, opaque selected checkpoint reference, source basis,
storage binding, declared policy and effect policy. Re-execution requires an
explicit acknowledgement; read-only requests reject that acknowledgement.
Branch values and attribution use the existing root update schema/task policy.
Replay cannot silently supply new values. A fixed source/request control identity
and content digest distinguish duplicate requests from conflicting commands.
Common descriptors contain no native checkpoint IDs, native state or plans.

## Read-only native basis

Preparation requires both the current request and retained original authority
for `state.replay` or `state.branch`, the original principal and archived workflow
identity. The target must be distinct and absent. It acquires no source lease,
creates no intent or target invocation and changes no source ownership generation.

Bounded native inspection locates the selected historical root by its opaque
reference, including an earlier initializer. It validates the original input,
OWA projection, common receipts, checkpoint parent and native metadata. It follows
only the transitive completed-scope closure required by that root, verifies each
required digest and completion, and excludes scopes completed by later progress.
Fresh physical/archive/common validation follows native reads. Current authority,
target absence and storage binding are checked again before returning.

The immutable internal basis pins the exact checkpoint coordinates, metadata,
input and OWA projections under native byte/scope/deadline limits. Its digest
includes the private checkpoint ID without placing it in common descriptors.
Future forks must not inherit SDK pending writes; this preparation records their
presence but stages only the validated OWA projection. No from-start restart is
substituted for selected-checkpoint replay.

## Development evidence

Focused SQLite/PostgreSQL checks cover historical initializer/current/earlier
roots, completed scope closure and exclusion of later scopes, altered basis,
missing required scope, invalid target/reference/schema/attribution, resource
limits, absent/different/revoked authority, detached values and explicit
re-execution acknowledgement with zero dispatch/model calls. Compiler/command
checks cover exact declarations, immutable policies, strict descriptors and
unchanged legacy workflow fingerprints.

The first native development run retained eight failures: the inspection probe
needed the scope executor's coordinates, and test enumeration closed a saver
before fully consuming its SDK generator. Both were corrected. Subsequent
targeted checks pass six cases; the complete new native preflight plus existing
update preflight selection passes 56 cases. The two explicit re-execution cases
also pass. Compiler/workflow/admission regression passes 129 cases. Reports
remain in `tmp/merge-20261025/lineage-*`; frozen cross-platform qualification
follows separately. No task checkbox or research row is promoted by these checks.

## Remaining implementation and acceptance

Implement a fenced native fork under the new business identity, durable common
lineage/idempotency and recovery, checkpoint/scope binding remapping, branch data
attribution and effect enforcement before exposing controls. Prove read-only
effects cannot execute, explicit re-execution creates new receipts, interruption
and restart do not duplicate work, and parent history/authority remain intact.
Public admission, lifecycle budgets, configured examples, full T49 and broader
S4-02/time-travel qualification remain required. Broader memory/stream/native
scope gates and [state-control acceptance](s4-02-native-state-acceptance.md) remain
independent.


## Frozen source qualification — `f018d7f`

Runtime/tests/examples are frozen at
`f018d7f17256c41c41445fb76d046e1fbdb8b254`. The combined cohort adds 32
compiler/command cases, 32 actual selected-basis cases, two explicit
re-execution acknowledgement cases and four LangGraph admission cases to the
previous 420-case state-control/native/authority regression. All required
SQLite/PostgreSQL cases run; other-engine cells are deselected explicitly.

| Selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Combined lineage/state controls/native/authority regression | 490 passed, zero skips | 490 passed, zero skips |
| Separate actual ADK authority/fencing/rejection | 26 passed, zero skips | 26 passed, zero skips |
| SDK-free common compiler/journal/authorization/recovery/API/configuration | 881 passed, zero skips | Not selected |

Windows and Linux use separate fresh PostgreSQL databases. Existing immutable
SDK dependency images retain matching locks. Linux runs without package
installation, with read-only source/root filesystems, a non-root user, dropped
capabilities and resource limits. All 1,286 frozen Linux inputs remain unchanged.
Logs, XML, manifests and machine-readable results remain under
`tmp/merge-20261025/f018d7f/`. The combined cohort finishes in 792.60 seconds
on Windows and 320.62 seconds on Linux; the SDK-free cohort takes 270.85 seconds.
Formatting, Ruff, strict typing of 180 core/ADK/LangGraph source files, Markdown
links, task tracking and whitespace checks pass. Final documentation changes
leave frozen runtime/test/example inputs unchanged.

This is source qualification for private policy and read-only basis preparation.
It does not qualify native fork execution, effect enforcement, public replay or
branch controls, published images, full repository/release acceptance or task
completion. Task counts remain 31/37 verified, six open, none deferred. The
conditional S4-09/S4-10/S4-11 tracks remain conditional.
