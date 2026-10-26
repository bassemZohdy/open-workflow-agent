# S4-02 retained component notes

Extracted 2026-10-26 after PR #91. These chronological notes preserve the
verified history and earlier next-step statements. [TODO.md](../../TODO.md)
and [PROJECT.md](../../PROJECT.md) describe current work; dated candidates below
are not additional completion claims. Detailed acceptance records retain the
source-matched results and failed attempts.

## Previous backlog narrative

Bounded channels/reducers, Command and Send are implemented with focused
native/store recovery evidence. The owned wait/end transition and native test
profiles are corrected. The final direct-child stop fixture passes eight
Linux SQLite/PostgreSQL routing/scope cases without pidfd APIs and four
Windows PostgreSQL cases. Hosted qualification of that repair remains open.
PR #76 merged the private memory foundations into main at `56e68b0`; PR #77
merged common recovery at `df8c0d7`. Source `54103b9` adds prepared/dispatched/completed write
intents, owned completion receipts and native proof checks, plus an authorized
private writer. The 322-case reader/write/journal/ownership cohort passes on
both Windows and frozen Linux with zero skips. Native commit without common
acknowledgement recovers without rewriting; dispatched writes with no marker
fail as uncertain and are never blindly redispatched. Read observations,
common intents and native markers each have a 64-per-invocation ceiling.
PR #78 merged expiry at `b344058`. Source `810c723` passes a 355-case memory/journal/ownership
cohort on both Windows and frozen Linux with zero skips. A typed policy uses
database time to commit the record deadline with its value and native marker;
fresh get reads omit expired records while replay retains past observations.
Retries do not renew expiry. PR #79 merged capacity/cleanup at `3187cfe`.
Capacity/cleanup source `6a0ac8c` passes 399 cases
on each platform with zero skips. Fixed namespace record/serialized-value byte
budgets and authorized bounded physical expiry cleanup retain recovery proof.
PR #80 merged provider lifecycle at `1a0b0a9`. Source `738b393` passes 415 cases
per platform with zero
skips, adding concurrent first-use migration exclusion and setup/shutdown
cancellation, timeout and process-exit witnesses.
Resolution/checkpoint source `602449e` passes 523 cases per platform with zero skips. A
private preauthorized control atomically records completion from matching
native proof or explicit unknown-outcome abandonment; neither redispatches
the write. Audit, phase and outcome survive process exit and reject corruption.
It preserves long accepted task contexts and serializes native checkpoint
first-use migrations. Eight shared authority cases pass on each platform
with fresh PostgreSQL databases.
Source `c062e9c` adds opt-in configured get/put/search/delete/sweep calls,
scalar-filter search and original-owner authenticated resolution with actual
workflow resume. Default admission remains closed; configured native bindings
enable the exact required version. It also repairs early platform rejection
and concurrent cold ADK PostgreSQL schema setup. The [public qualification](../s4-02-native-memory-acceptance.md#public-workflow-profile--2026-10-26)
retains failed attempts and the final named acceptance limits. Semantic
profile `a781bc2` adds declared local MiniLM/exact-index binding, versioned
semantic queries, actual artifact identity and atomic value/vector recovery.
Both stores pass focused restart, process-exit/newer-write, original-owner
resolution, expiry/filter/namespace and physical vector cleanup checks. Its
combined cohort passes 638 cases with zero skips on each of Windows and
frozen Linux; 1,222 Linux source inputs remain unchanged. See
the [semantic qualification](../s4-02-native-memory-acceptance.md#semantic-workflow-profile--2026-10-26).
Continue broader T51/T68/public recovery/model migration gates,
native streams, public state updates, replay and time travel. T51/T68 and
S4-02 remain unchecked; the three product-dependent tasks stay conditional.
See the [memory scope](../s4-02-native-memory-acceptance.md).
Then continue streams, public state updates, replay and
time travel. Preserve common authority, ownership, receipts and private
checkpoint boundaries. See [acceptance](../s4-02-native-channels-acceptance.md)
and the [integration record](../validation-history/main-integration-2026-10-25.md).
After PR #83, continue the [private native-stream foundation](../s4-02-native-streams-acceptance.md),
whose `baebb73` selection passes 106 cases on each platform with zero skips,
followed by authorized public transport and its capability/qualification gates.
The follow-on public values/updates profile and Windows native-write loop
repair are [qualified at a597134](../s4-02-native-streams-acceptance.md#replacement-public-qualification--a597134):
318 combined cases and 18 separate ADK cases pass per platform with zero skips,
with 1,237 unchanged Linux source inputs. The SDK-free 242-pass baseline adds
46 separately verified PostgreSQL cells, closing its initial skips.
Broader stream modes, T50, state updates, replay and time travel remain open.
After PR #85, [state/history reads](../s4-02-native-state-acceptance.md)
provide bounded original-owner native views and stable opaque checkpoint
references. Frozen `f060349` passes 285 combined cases and 20 separate ADK
cases on each platform, plus 312 SDK-free core/API cases, with zero skips
and 1,250 unchanged Linux inputs. Continue compare-and-update intent/proof,
native atomic mutation and new-identity replay/time-travel lineage.
PR #86 is merged; private atomic SDK checkpoint/write/proof storage is now
qualified at `3bf27d7`: 99 cases per platform, zero skips, 1,253 unchanged
Linux inputs. It enables no mutation route or capability;
management intents/reconciliation and declared update policy remain required.
PR #87 is merged; the private common journal and native proof bridge now cover
authorized intents and interrupted acknowledgement recovery. Qualification is
recorded at `7572808`: 174 combined cases per platform and 653 SDK-free
core/API cases, zero skips, 1,257 unchanged Linux inputs. Keep public
policy/schema/task attribution, workflow snapshot/receipt
integration, unknown-control resolution and continuation/branch gates open.
PR #88 is merged; compiled declared update policy and read-only OWA native
preflight now validate schema/attribution, owner/lease, exact current base and
retained completed-scope evidence. Frozen `8393d1e` passes 278 combined cases
per platform and 755 SDK-free compiler/core/API cases with zero skips and
1,262 unchanged Linux inputs. Update admission
remains closed; continue public ingress, atomic OWA snapshot/receipt
attestation, unknown-control resolution and continuation/branching.
PR #89 is merged. The next private component binds exact base/result digests
and ordered control identities to SDK-updated OWA data and common receipts.
Focused SQLite/PostgreSQL continuation and fresh-process recovery checks pass;
previous visits, routes, counters and native channels remain intact. Frozen
`1a546e6` passes 428 combined cases and 22 separate ADK cases on each platform,
plus 763 SDK-free cases, with zero skips and 1,266 unchanged Linux inputs. Public
ingress, unknown-control resolution and full continuation/branch acceptance
remain required. See the [attestation scope](../s4-02-native-state-acceptance.md#attested-owa-data-updates-after-pr-89).
PR #90 is merged. Private explicit resolution now records request-bound
original-owner decisions without native redispatch. Proof-backed completion
permits private continuation; acknowledged unknown abandonment blocks adopting
a current native head. Common audit/outcome rollback, immutability, current and
original permission, actual OWA state and fresh-process checks pass on both
stores. Frozen `0f9312f` passes 384 combined cases and 22 separate ADK cases
per platform, plus 827 SDK-free core/API cases, with zero skips and 1,268
unchanged Linux inputs. Continue public configured ingress and broader continuation/branching;
see the [resolution scope](../s4-02-native-state-acceptance.md#explicit-state-control-resolution-after-pr-90).

## Previous project narrative

Private native memory foundations, common write recovery, record expiry,
namespace capacity/cleanup and provider lifecycle are merged through PRs
#76–#80 (`56e68b0`, `df8c0d7`, `b344058`, `3187cfe`, `1a0b0a9`). Each required
hosted Docs check passed before merging; the integration record retains exact
heads and runs. SQLite is embedded by default; PostgreSQL remains optional.

Bound reads retain the first committed value or absence under original
authority. Fenced SDK transactions retain native markers, while common
prepared/dispatched/completed intents govern recovery. A matching native marker
can finish interrupted common completion; missing proof never permits blind
redispatch. Each invocation has ceilings of 64 read observations and 64 writes.
Typed database-clock expiry and namespace record/serialized-value byte budgets
bound fresh data. Authorized bounded physical cleanup retains recovery proof.
Cross-worker setup exclusion, cancellation and process-exit lock recovery have
finite evidence. Historical common payloads and markers are retained.

Source `602449e` adds a private preauthorized resolution control. It atomically
records completion from matching native proof or acknowledged abandonment with
an explicitly unknown outcome. Decisions are immutable and never redispatch a
write. It also repairs concurrent first-use checkpoint migrations. Its
**523-case** memory/journal/ownership/lifecycle/resolution/native-checkpoint cohort
passes on both Windows and frozen Linux with zero skips and 1,198 unchanged
Linux source inputs; eight shared authority checks pass on each platform using
fresh PostgreSQL databases. Earlier cohorts, failed attempts and focused qualification
limits remain in the [native memory acceptance record](../s4-02-native-memory-acceptance.md).
This is component evidence, not full repository/release qualification.

Source `c062e9c` connects native memory to opt-in versioned workflow functions,
bounded scalar-filter search and authenticated original-owner recovery controls.
Configured bindings enable the exact capability; default installations continue
to reject it. The [public profile and qualification](../s4-02-native-memory-acceptance.md#public-workflow-profile--2026-10-26)
record the actual stores/platforms and retained failed attempts. The source also
restores early unsupported-loop rejection and repairs concurrent cold ADK
PostgreSQL schema setup. Terminal invocations remain immutable; no resolution
control dispatches native writes. See the [configuration-only guide](../native-memory.md).
Source `a781bc2` adds a separate declared local-model/index
profile and `memory.search:1.1.0` while retaining filtered search's v1.0 contract.
It fingerprints installed model/inference artifacts, commits native vectors with
the value and marker, and retains common observations and resolution authority.
Actual process-exit recovery, model drift rejection, physical vector cleanup
and both HTTP resolution choices pass focused checks on SQLite/pgvector. The
packaged configuration-only API example passes with the real local model,
including SQLite with networking disabled. The combined suite passes 638 cases
with zero skips on each of Windows and frozen Linux, with 1,222 unchanged Linux
source inputs. Its frozen qualification is tracked
in the [semantic acceptance section](../s4-02-native-memory-acceptance.md#semantic-workflow-profile--2026-10-26).
Broader T51/T68, online reindexing, other model/index profiles, native streams,
state updates, replay and time travel remain open under S4-02. No task or
research inventory row is promoted by this finite component profile.

PR #84 merges the private bounded native-stream observation transport and
projection from the pinned SDK. See the [native-stream scope](../s4-02-native-streams-acceptance.md).
Its frozen `baebb73` private component selection passes 106 cases on each of
Windows and Docker/Linux with zero skips and 1,226 unchanged Linux inputs.
The follow-on [public source profile](../s4-02-native-streams-acceptance.md#replacement-public-qualification--a597134)
at `a597134` adds opt-in original-owner SSE, explicit required admission and
bounded process-local retention. It repairs Windows worker-thread loop selection
for fenced native writes while preserving early unsupported-loop rejection.
The combined 318-case stream/native/ownership/write-recovery selection and 18
separate ADK authority/fencing/rejection cases pass on each platform with zero
skips; 1,237 frozen Linux inputs remain unchanged. The 242-pass SDK-free baseline
adds 46 separately verified PostgreSQL cells, closing its initial skips.
Other stream modes, full T50, native state updates, replay and time travel remain
open; no task or research row is completed by this finite source profile.

After PR #85, the [state/history profile](../s4-02-native-state-acceptance.md)
adds original-owner root state/history views using read-only native snapshots
and opaque references that survive process restart. These references are the
prerequisite for compare-and-update/branch controls, not authority to execute
them. Frozen source `f060349` passes 285 combined cases per platform on Windows
and Docker/Linux, 20 separate ADK cases per platform and 312 SDK-free core/API
cases, all with zero skips. All 1,250 frozen Linux inputs remain unchanged.
Native writes, replay/time travel and full T45/T49 remain open; this finite
component does not complete S4-02 or qualify a release.

PR #86 is merged at `2649646`. The next private prerequisite groups native SDK
state-update checkpoints, pending writes and proof in one owned transaction,
with compare-and-update and repeat detection. Frozen `3bf27d7` passes 99 cases
on each platform with zero skips and 1,253 unchanged Linux inputs. It is not
registered or advertised; common management intent/recovery and declared update
policy remain required in the [state-control record](../s4-02-native-state-acceptance.md).

PR #87 is merged at `a4462d0`. The private common control-journal candidate adds
original-owner request-bound permission, durable prepared/dispatched/completed
intents and native-proof reconciliation without duplicate dispatch. Core retains
only commands and opaque references. Focused common-store and real SDK bridge
checks pass. Frozen `7572808` passes 174 combined cases per platform and 653
SDK-free core/API cases with zero skips and 1,257 unchanged Linux inputs.
Separate ADK authority/fencing/rejection checks pass 20 cases per platform with
zero skips. Public workflow update semantics, admission, unknown-control
resolution and continuation/branching remain open.

PR #88 is merged at `2d972b0`. The next candidate compiles declared update
policy/schema/attribution into the immutable plan and performs read-only
preflight over actual OWA waiting state and completed-scope evidence. Original
owner, permission, live ownership and exact current-base checks precede mutation.
Frozen `8393d1e` passes 278 combined cases per platform and 755 SDK-free
compiler/core/API cases, with zero skips and 1,262 unchanged Linux inputs.
Separate ADK checks pass 22 cases per platform with zero skips. Required update
admission stays closed until public ingress and atomic updated OWA receipt/snapshot
attestation, resolution and continuation are implemented and verified.

PR #89 is merged at `f931945`. The next private component applies declared data
replacement through the pinned native SDK. Versioned common commands bind exact
base and resulting projection digests; root receipt bindings retain ordered
control identities. Atomic native verification preserves earlier task history,
routes, counters and channels. Private continuation uses the changed value without
repeating earlier tasks. Fresh-process prepared-command recovery, interrupted
acknowledgement and completed-proof inspection pass on SQLite and PostgreSQL.
Frozen `1a546e6` passes 428 combined cases and 22 separate actual ADK cases
on each platform, plus 763 SDK-free core/API cases, with zero skips and 1,266
unchanged Linux inputs. Public ingress, unknown-control resolution and full
continuation/branch acceptance remain open. See the [attestation record](../s4-02-native-state-acceptance.md#attested-owa-data-updates-after-pr-89).

PR #90 is merged at `1e83e14`. The next private component adds explicit,
request-bound `state.resolve` decisions for dispatched state controls. Matching
native proof permits completion; acknowledged abandonment records an unknown
outcome and never establishes a safe head for continuation or another update.
The resolution audit and control outcome commit together. Original/current
permission, original owner and a live execution lease remain required. Focused
common-store and actual OWA/fresh-process checks pass on SQLite/PostgreSQL.
Frozen `0f9312f` passes 384 combined cases and 22 separate actual ADK cases per
platform, plus 827 SDK-free core/API cases, all with zero skips and 1,268 unchanged
Linux inputs. See the [resolution scope](../s4-02-native-state-acceptance.md#explicit-state-control-resolution-after-pr-90).
Public configured ingress and broader continuation/branch gates remain open.
