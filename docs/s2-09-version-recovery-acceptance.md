# S2-09 original-definition continuation and recovery policy

Status: implementation and test authoring complete; batch validation pending. S2-09 remains unchecked;
no new continuation profile is qualified until its complete finite validation
and hosted gates pass. This record defines supported decisions and exclusions,
not a successful in-place migration algorithm or full A086/T44 completion.

## Supported continuation

An existing invocation retains its originally admitted full definition
fingerprint, public document version, captured catalog/function/schema content,
original raw input and public/common identity. New deployments may select v2 for
new invocations while old runs continue v1. A semantic-version label, similar
graph shape or matching workflow name does not establish replay compatibility.

The common archive contains bounded public definition inputs, captured resource
content and private execution-profile evidence. It does not own native
checkpoints or expose the internal execution plan. Archive and original input
binding commit under current execution ownership before first native projection
or business dispatch. Fresh REST/A2A resumes resolve that retained definition
without fetching a mutable catalog or replacing input with a resume message.
Missing, changed or incompatible evidence rejects before execution authority or
native mutation. Current catalog/schema/endpoint/capability authorization still
applies; retained content does not override a revoked deployment trust decision.

Immutable invocation binding is keyed by the full fingerprint, including captured
dependency content. Existing explicit WorkflowCatalog registration forbids
replacing a registered version with different content. There is no new global
namespace/name/version uniqueness rule: independent fresh admissions or captured
dependency revisions may coexist under the same public version with different
fingerprints, as the existing contract permits. Neither may reinterpret the
other's existing invocation.

Invocation ID, session, correlation user, authenticated owner, original input,
native reference, receipts/action keys, original absolute deadlines and child
budgets remain unchanged by deployment selection. Completed/faulted/cancelled
common outcomes, including null, remain readable after object authorization
without an executable legacy archive. That read does not validate native history.

## Compatibility and explicit unsupported mapping

Continuation requires an exact qualified installed execution profile: core and
adapter code/artifact digests, relevant lock/metadata/dependency versions, Python
runtime/platform profile, engine, native binding/serializer and storage backend,
and qualified nonsecret deployment semantics. Build provenance is included in
production packages; editable provenance is explicitly bounded. A package label
alone or a convenient neighboring checkout is not compatibility evidence.

Changed or unavailable profiles reject. An unqualified configurable model/tool
binding may permit ordinary initial execution but does not promise durable
continuation; explicit deployment-owned qualified binding versions are required.
Resolved secrets are neither archived nor returned in recovery diagnostics.

In-place source-to-target fingerprint migration, checkpoint/task renaming, engine
transfer, arbitrary SDK/schema upgrade and downgrade, database reset and an
automatic compensation/replay fallback are unsupported. Public resume accepts
only resume input. A caller-supplied different plan is rejected by the common
definition-changed contract. Any future migration requires a separately approved
immutable source/target mapping, authenticated management authority, atomic
fencing, retained provenance and actual backend/native acceptance. No native
checkpoint rewriting or new Open Workflow DSL field is introduced here.

A crash immediately after common archive admission but before the first native
checkpoint does not authorize public resume to invent that history. It fails
closed without writes or business I/O. A trusted deployment owner may explicitly
redeliver an initial invocation with its verified original archive/input after
ownership becomes available; that is a distinct existing initial invocation path,
not an automatic public resume or migration fallback.

Rollback is supported only to a separately qualified producer/consumer profile
and retained compatible persistence snapshot. Stop writers before snapshot
restore, retain reconciliation evidence, and account for remote effects.
Restoring local bytes cannot undo a committed remote action. No tested cross-SDK,
cross-database-server or arbitrary platform upgrade matrix is claimed.

## Public authorization and bounded diagnostics

REST resume/cancel/recovery inspection authenticate through the existing API
profile and match the invocation's owner principal. Unauthorized IDs use the
existing not-found response. Ownerless anonymous legacy compatibility is retained
only when API authentication is disabled. A2A retains its existing task/skill
authorization checks before archive resolution.

`GET /v1/invocations/{invocation_id}/recovery` returns the versioned
`owa/recovery-diagnostics/v1` common projection: public identity, stored
name/version/fingerprint, sanitized checks/conditions, bounded known/pending action
counts and observed ownership availability. It never acquires/renews a lease,
dispatches/resumes, wakes a timer/child, queries a receiver or initializes a
native database. Action inspection is capped at 100 index entries, 8192-character
identity records and 65536-character receipts before decoding. Exceeded bounds
are explicitly incomplete. Failure details are selected from fixed reason values;
SDK/SQL exceptions and stored payloads are not echoed.

Native readiness is deliberately `not_inspected`: existing pure native presence
guards reject initial re-admission but do not prove full SDK checkpoint
consistency. Normal resume still performs complete qualified native admission.
Diagnostics never expose native IDs/blobs, source/internal plan, inputs/outputs,
HTTP bodies/headers, receiver proof, configuration, credentials or secret values.

## Required finite evidence

| ID | Required bounded proof and owner |
| --- | --- |
| VM01 | Actual ADK/LangGraph, SQLite/PostgreSQL: public REST/A2A resumes retained waiting v1 under a v2-only deployment/reopen; fresh admission uses v2; one original receiver commit and original identity/input/null remain. Shared version fixture and actual producer-stop fixture. |
| VM02 | Archive/binding/digest/input/dependency/profile/trust mutation or absence rejects without common/native/ownership mutation or new dispatch. Core archive proof plus shared public corruption/runtime-profile/native-only legacy proofs. |
| VM03 | Different-fingerprint and caller-supplied mapping rejected without reinterpretation. Successful explicit in-place mapping remains unsupported/unverified. No public checkpoint transport. |
| VM04 | Original receipts/keys, principal/session/input, absolute deadline/due times and qualified child bindings/budgets preserved by continuation. Shared version assertions plus retained S2-03 through S2-08 qualified invariants. Terminal legacy value/null/fault/cancelled reads are immutable. |
| VM05 | Real acknowledged producer PID terminated without graceful cleanup at archive-only and native-waiting boundaries; distinct process uses v2 deployment; archive-only public resume rejects unchanged, trusted original initial redelivery remains distinct. Admission transaction rollback and prior effect/native process-stop proofs retain their scope. |
| VM06 | Read-only deterministic bounded common diagnostics, pending/known distinction, safe profile/missing evidence reasons, ownership observation, unauthorized-ID hiding and secret-safety. Native inspection unavailable is explicit; no full native-history diagnostic claim. |
| VM07 | Installed editable/production artifact provenance and exact profile comparison; unsupported changes reject. Build/package checks qualify packaged markers. No arbitrary SDK/database migration or rollback success is implied. |
| VM08 | Same actual principal fixtures/results on SQLite/actual PostgreSQL; root quality/coverage/schema/build, frozen full suites, Windows/images/security and exact-source hosted acceptance. Optional Framework and JavaScript/full native history retain independent gates. |

Evidence will be added after the authored batch freezes and passes. Core tests
and shared fixtures use deterministic fakes, a durable receiver uniqueness key
and bounded actual process stops; no paid model/provider calls are required.

## Inventory limits

A086 concerns `document.version; no migration algorithm`, with the tracked gap
`Pin old definition for suspended runs; explicit migration policy required`.
This work defines and tests pinned original continuation and rejection policy.
It does not mark an in-place migration algorithm, JavaScript or full-feature
operational equivalence as verified.

T44 requires suspending v1, deploying incompatible v2, resuming pinned v1 **and**
explicit migration only with an approved mapping, preserving fingerprint and
dependency versions without silent reinterpretation. VM01/VM02/VM03 prove the
pinned-continuation and negative unsupported-mapping portion. A successful
approved source-to-target mapping remains unverified. T44/A086 must retain that
remaining scope even if the finite S2-09 **Define** task is later validated.
T45 diagnostic correlation does not activate native history export, earlier
checkpoint replay, state edits/forks or later-stage capabilities.

## Initial recorded batch -- 2026-10-13

Frozen source 65a26db runs 394 fixed inputs. Root-focused verification passes
58 cases with 29 unavailable PostgreSQL skips and one fixture failure: the
foreign-owner resume case sent an invalid empty request rather than its required
input field, producing schema validation before the intended object check.
Both actual native engines pass retained public version cases before this
attempt is explicitly interrupted at 227 seconds for an actionable provenance
read-cost correction. Original logs, hashes and interruption records are retained
in `tmp/s2-09-first-batch`; interrupted suites are not successful full runs.

Independent profiling isolates repeated oversized allocation in source-file
reads. The correction preserves fresh exact hashes, bounded reads and metadata
checks rather than relaxing recovery compatibility. All five source/wheel builds
and their source-lock/metadata provenance inspections pass. Final corrected
focused, full-suite and hosted qualification remain required.

The optional-adapter development profile is explicitly common fallback. It
requires the installed root editable declaration, exact adapter source origin
under that project, and independent lock/metadata/code hashes while the native
SDK is absent. Production wheels, standalone core, foreign origins and an
installed SDK without its adapter cannot use this path. Installing the SDK
changes the profile and requires native provenance; fallback is not native
qualification. Twelve source/provenance negative cases are authored.

The exact-size read correction measures 0.52 seconds cold and 0.27 seconds warm
per LangGraph profile, compared with approximately 3.1 seconds before correction.
It retains fresh source hashing and checks the opened file metadata before and
after its size-bounded read. Growth, truncation, mutation and oversize tests are
included in the corrected batch.
