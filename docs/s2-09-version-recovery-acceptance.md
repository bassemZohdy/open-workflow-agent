# S2-09 original-definition continuation and recovery policy

Status: implementation and test authoring complete; batch validation pending.
S2-09 remains unchecked until its complete finite validation and hosted gates
pass. This record defines supported decisions and exclusions, not a successful
in-place migration algorithm or full A086/T44 completion.

The latest source checkpoint, `c68950f`, is not fully qualified. Its CI run
`38551999533` was cancelled when the Windows timer lane reached its 35-minute
job limit after pytest reported 686 passed and 674 skipped with no assertion
failures. Fifteen of sixteen CI jobs passed. The workflow now gives that lane
45 minutes; a fresh exact-source hosted run is required. PostgreSQL is
`38551999491`, Docs is `38551999483`, Security is `38551999498`, External
Sandbox is `38551999473`,
and Agent Framework is `38551999501`. The Docs, Security, and External Sandbox
runs passed; Agent Framework and PostgreSQL also passed. The previous source
`605a908` failed one Windows LangGraph/SQLite initial-delivery recovery
case during admission; see the [batch history](validation-history/pending-batch-results.md#hosted-checkpoint-source-c68950f).
The local fixture uses a 15-second ownership lease to reduce admission timing
sensitivity. Recovery diagnostics identified an intermittent mismatch in
`python.architecture`: the restricted subprocess environment omitted the
non-secret Windows architecture variables used by `platform.machine()`. The
fixture now preserves those variables and records changed-profile paths for
diagnosis. After this correction the exact Windows selector passed three
consecutive local repeats, and the related LangGraph process-recovery/A2A
selection passed 14 tests with 14 PostgreSQL-only skips. Exact-source hosted
qualification is still required; recovery profile verification remains strict.

The combined `325458a` full local batch is retained as a failed unchanged-source
attempt in `tmp/s3-01-third-batch`, including actual ADK process-stop failure
with a hot attached SQLite rollback journal. The authored correction preserves
pre-lease read-only inspection of originals by recovering only a bounded stable
private copy after strict journal/master validation. It does not transplant a
checkpoint or authorize missing native history. Original-version child replay
fixtures now preserve the retained source/outcome when a mutable catalog changes.
These corrections require behavioral/native/backend/platform qualification
before this task or its finite continuation profile can be marked complete.

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

Rollback execution is not implemented or qualified by this task. A deployment
must not restore a persistence snapshot unless its producer/consumer profile
and compatibility have been qualified independently. Any operational restore
must stop writers first, preserve reconciliation evidence, and account for
remote effects; restoring local bytes cannot undo a committed remote action.
No cross-SDK, cross-database-server or arbitrary platform upgrade matrix is
claimed.

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

The first corrected full-suite attempt at d406398 is retained under
`tmp/s2-09-second-batch`. All four profiles are explicitly interrupted at
165 seconds with unchanged inputs after reproducible catalog composition and
positive SDK-prestart fixture gaps. The actual installed resolver trust map
must be used consistently for admission and profile comparison. The existing
positive prestart fixture must record real owned archive admission before
publishing native history; the no-retrofit guard remains. Next broad runs use
a finite early failure limit for diagnosis; only complete zero-failure suites
can qualify completion. No interrupted run is relabelled successful.

The next broad snapshot 8a87640 is retained in `tmp/s2-09-third-batch`.
LangGraph reaches its five-failure limit after 131 passes/73 skips: valid
in-memory saver presence, an overlapping probe setup/write, and the older
synthetic late-finalization admission seam. Other profiles are explicitly
interrupted at 349 seconds with unchanged source; the API schema snapshot also
needs its newly declared route. Native memory inspection now reads only existing
official saver maps without setup or mutation and adds three actual SDK tests.
The probe finishes its setup before the runtime writer starts. Synthetic
finalization records real leased common admission and keeps an explicit fake
SDK boundary; it is not counted as native qualification. All ten-second bounds
and outcome/identity/cancellation assertions remain. The versioned API snapshot
is regenerated and included in subsequent fixed-input manifests.

Correction focus 01d13a6 passes 85 root cases with 31 unavailable PostgreSQL
skips; actual ADK passes 16 of 17 selected version cases, and LangGraph passes
15 of 17. The
remaining nested-child fixture serializes unset Linux path defaults before
Windows validation, and one process fixture observes transient Windows file
sharing on its already atomic readiness publication. Both are fixture-only
corrections: retain explicit config fields and retry only missing/sharing reads
within the unchanged live-producer 100-second bound. Malformed JSON still
fails.
Original source hashes/logs remain in `tmp/s2-09-fourth-batch`. Synthetic late
finalization and memory corrections follow in the same supervised bundle; no
partial failed bundle is qualified as complete.

Corrected bundle 5ef4170 is green with 396 unchanged fixed inputs. Root passes
85 checks with 31 unavailable PostgreSQL skips. Both actual principal SDKs pass
all 17 selected version/process-stop cases (including nested archived child
continuation under a v2-only catalog) without skips. Each passes both synthetic
late-finalization cancellation cases. LangGraph also passes the five memory
presence/fork regression cases. Supervisors complete in 33 seconds (root),
191 seconds (ADK) and 149 seconds (LangGraph), below their unchanged
900-second bound. All five fresh wheel/sdist builds and source-lock/metadata
provenance checks pass. Full broad suites
and exact-source hosted backend/platform/image/security gates still remain;
S2-09 is not complete and no pending source is published as qualified.

Broad snapshot 9582fb2 retains unchanged inputs under `tmp/s2-09-fifth-batch`.
Three old receipt selectors assume no independent definition-admission receipt;
they must target their intended HTTP/fork/clock evidence and preserve the new
admission proof. Nested workflows also repeatedly revalidate identical archived
public definitions against the official schema. Stack dumps locate the repeated
`_plan`/`validate_schema` path; nested cases time out and both principal Windows
processes exit with code 3221225477 during recursive schema traversal. Optional
Framework reaches its early failure limit; root is explicitly stopped at 820
seconds. These failures are retained and are not complete full qualification.

The correction caches only bounded successful identical strict-JSON validation
for the same official validator. First/changed/invalid inputs and custom captured
registries retain validation. Schema bytes, capability checks, profile matching
and current trust verification remain unchanged. Corrected fixture selectors
retain original negative recovery, immutable admission and no-replay assertions.

Correction snapshot `72fa61f` passes 97 root checks with 31 unavailable
PostgreSQL skips, all 17 selected actual version/process cases per principal,
the synthetic cancellation regressions, LangGraph memory/fork regressions and
13 nested/leaf/deadline cases per principal. All five fresh builds pass. The
next broad run preserves 397 unchanged inputs under `tmp/s2-09-sixth-batch`.
LangGraph stops with five detached-producer admission failures after 432 passes
and 199 skips; optional Framework stops with the same five failures after 526
passes and 283 skips. Root/ADK runs are explicitly interrupted after the common
guard defect is established; they are not complete successful suites.

The recovery trust check newly exercises the existing sandbox capability gate
for every original definition. Its executable-run helper incorrectly applies
process background/return restrictions to standard child workflows. Restricting
that helper to script/shell/container retains executable restrictions while
allowing the existing common workflow lifetime semantics. Unit guard cases and
actual detached-child/process-stop regression cases accompany the correction.
No timeout, admission-proof requirement or validation gate is weakened.

The corrected `7a3f9ab` broad snapshot is retained in
`tmp/s2-09-seventh-batch`: root reaches 638 passes/464 skips, LangGraph
602/402 and optional Framework716/548 before their five-failure event-admission
limit. ADK is explicitly stopped after the same actionable defect is established.
Inputs remain unchanged; none is a complete successful broad suite. The new
preparation guard moved a known unsupported event rejection outside the former
invocation fault-result boundary. That boundary is restored before input binding,
archive admission or native execution. Strict recovery-evidence rejection remains.

Three old waiting fixtures are corrected together: protected leased admission
for deliberately synthetic shutdown/event capability seams, and real acknowledged
wait execution for A2A continuation. Existing input, no-dispatch, shutdown and
task identity assertions are retained. New MCP work is authored separately;
final combined source qualification must cover this recovery task and the MCP
batch without relabelling earlier partial failed attempts as successful.
