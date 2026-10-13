# Pending batch results — 2026-10-13

These attempts do not close S2-09 or S3-01 through S3-08. Passing subsets and fresh
builds are distinct from complete qualification. Local `tmp/` evidence is ignored
and retained in its originating worktree; publication still requires reproducible
source-linked hosted reports.

| Frozen source | Profile | Recorded result | Meaning |
| --- | --- | --- | --- |
| `67aa724964edadb661f809bdde6949ce5af69b0f` | Hosted checkpoint | Aggregate failure; individual image/security/platform/database checks passed. | Draft PR checkpoint remains unqualified. |
| `f2220334c90e0d853fb348817fea5cbb3f08b218` | Root / ADK / LangGraph / optional Framework | 44 failures, 716 passes, 140 skips, 2 errors / 20 failures, 115 passes, 246 skips / 20 failures, 115 passes, 246 skips / 59 passes, 358 skips. | First operation batch; 979 inputs unchanged. |
| `9570557316014542fe10bfbd2361f70e2968df05` | Root / ADK / LangGraph / optional Framework | 22 failures, 755 passes, 140 skips / 10 failures, 260 passes, 295 skips / 6 failures, 264 passes, 295 skips / 3 failures, 251 passes, 429 skips. | Grouped correction batch; all four ended naturally with 980 inputs unchanged. |
| `6622ab08be0015ff143adb377a58709124d30c24` | Common executable-source and operation batch | 14 failures, 1,028 passes, 202 skips, 2 errors. | First executable-source common batch; 994 inputs unchanged. Principal executable-source profiles had not run at this source. |

Five independent package/provenance builds passed at each of these three local
operation/executable sources. They do not substitute for behavioral, native,
actual PostgreSQL, platform, image, security or publication gates.

Current corrections address retained wire/fixture mistakes, bounded input case
identifiers, actual source authority, stopped Windows process outcomes, gRPC
metadata and atomic leased completion. Fresh background-recovery diagnostics
identified synchronous profile verification delaying renewal of the original
five-second lease. A cooperative correction passed one actual LangGraph
producer-stop/recovery diagnostic with the unchanged lease, original input and
deadline, one business commit and distinct process identities. Broader regression
and hosted qualification remain pending; no timeout or acceptance threshold was
relaxed. A transient unknown supervisor observation remains historical; a separate
seven-second diagnostic is not qualification of the original shorter fixture.

Required next evidence and exclusions remain in [S3-03](../s3-03-operation-adapters-acceptance.md)
and [S3-04](../s3-04-executable-source-acceptance.md). Current completion and deferral
status remain in [PROJECT.md](../../PROJECT.md) and [TODO.md](../../TODO.md).

## Combined source, operations and approvals — `ebc2694`

The immutable local batch retains 1,016 tracked-input hashes. The common profile
ended with 23 failures, 1,089 passes and 227 skips in 528.37 pytest seconds
(545.19 seconds including driver/startup). Its end manifest records no tracked
source changes. All five fresh independent package build/provenance checks passed.
The optional Agent Framework profile finished with 254 passes and 565 skips in
581.03 pytest seconds; its end manifest records no tracked source changes.
Principal-only profiles skipped in this optional environment remain unqualified.
LangGraph finished with 30 failures, 274 passes and 397 skips in 1,165.04
pytest seconds (1,198.55 driver seconds), with no tracked source changes.
ADK finished with 30 failures, 274 passes and 397 skips in 1,344.40 pytest
seconds. Its end manifest records no tracked source changes. All four behavior
profiles have ended; the three failed profiles require a coherent correction batch.

The common failures identify five fixture calls to a nonexistent identity
projection, one fault fixture lacking required common error details, sixteen
protected-approval paths hashing tuple-based dataclass identity projections before
finite-JSON serialization, and one timer test assuming two renewals where one
unchanged five-second renewal already covered the seven-second workload. Source
corrections preserve strict JSON validation, actual original lease duration and
ownership checks. They are authored in the separate S3-06 checkout and remain
untested until that coherent batch is complete. This record qualifies no task.

The LangGraph failures also expose six reusable inline-script identity lookups
using a public reference where the private execution reference is needed; three
captured-script business-ledger assertions expose Windows Python stdin decoding
UTF-8 as the system code page, despite the returned stdout appearing correct;
and one source-proof corruption path acquires ownership before rejecting damaged
independent evidence. Protected approval native/process cases share the tuple
serialization issue and require review of producer ownership failures. These are
actionable correction work, not task deferrals. Original lease durations, captured
byte digests, public task references and fail-before-mutation gates remain required.

## Frozen S3-06 broad validation — dc71ecb

The combined correction/S3-06 source at `dc71ecb` freezes 1,034 tracked inputs.
All five fresh build/provenance checks passed. The broad common profile ended
with **46 failures, 2,684 passes, 591 skips and 59 setup errors** in 1,160.53 pytest
seconds (1,176.51 driver seconds), with no tracked input changes. The isolated ADK, LangGraph and optional Agent Framework profiles have also
ended; their final outcomes and bounds are recorded below.

Fifty setup errors came from a new fixture using occurrence zero where the
existing common effect identity requires a positive visit; two standalone journal
checks supplied an empty occurrence. Four API fixture errors lacked an explicit
fixture import. Repeated legacy component failures exposed an optional audit
participant treating unrelated standalone receipt history as native admission
history. The correction permits only default-disabled, unbound legacy services
with no current authority, candidate, original marker or independent authorization
proof; engine archive preflight and protected history remain fail closed.

Two PostgreSQL selector checks still expected 18 sandbox cases after five real
native return-projection cases were added (23 per engine). Other protocol/security
fixture failures have actionable fixes. Corrections and S3-07 source are authored
in a separate checkout; this failed frozen source is preserved. No completion or
CI qualification follows from these partial passes.

Source review of retained native failure evidence found a concrete engine filter
gap: independently verified original-authority receipts use the `/do` admission
scope, but both native adapters accepted only task references and the root clock.
LangGraph's initial SDK starter proof also retained a three-receipt bound while
valid authority admission can add a fourth internal receipt. Corrections must
allow only the exact original authority identity after common proof verification,
preserve all physical SDK starter checks, and continue rejecting business progress
or unrelated scope/policy records. These corrections remain source work until the
next frozen batch; those failed native runs are not retroactively qualified.

The remaining `dc71ecb` profiles have ended with unchanged tracked inputs.
Optional Agent Framework failed with 3 failures, 1,696 passes and 2,152 skips in
3,799.22 pytest seconds (3,831.87 driver seconds); the failures use a Windows
script fixture requesting an unsupported awaited file-size control. The runtime
rejection is preserved, and the positive fixture explicitly opts out of that
unavailable control on Windows in the next source batch. Both full principal SDK
profiles reached their original 4,500-second batch bound (exit 124), not a green
aggregate: ADK logged 1,266 passes, 33 failures and 1,422 skips; LangGraph logged
1,407 passes, 59 failures and 1,478 skips before termination. These are observed
progress counts, not final pytest totals or qualified test unions. Their artifacts
retain source hashes and full commands. Future isolated profiles exclude only
parameter instances belonging to the other unavailable SDKs; those instances run
in their own actual native environment, preserving shared fixture coverage.

## Frozen S3-07 and corrections — 3aaaf30

Commit `3aaaf30` freezes 1,054 tracked inputs. Static lint/format/type checks,
all 12 recipe schema/admission checks and all five fresh build/provenance checks
passed. The broad common run ended with **8 failures, 2,822 passes, 601 skips and
52 errors** in 1,037.85 pytest seconds (1,046.31 driver seconds), with no source
changes. Fifty errors were the new core role-boundary module requesting a fixture
scoped under the contract directory; two teardown errors released leases already
deliberately released by expiry/takeover tests. Remaining failures identify a
malformed-receipt exception escaping the common error contract, another legacy
unbound intent lookup, and an independent supervisor deadline observation gap.
The role fixture wiring, receipt translation, intent lookup and intentional stale
cleanup are corrected in the separate S3-08 checkout. Read-only verification of the retained HMAC observations found both original
deadline cases eventually terminal with the original deadline cause; the helper
returned an intermediate unknown observation too early. Healthy-terminal tests
now wait for terminal within the unchanged 20-second bound, while explicit
resource-failure cases still require unknown. The original seven-second execution
deadline and all production controls remain unchanged. Independent native
supervisor startup still needs diagnostic evidence; no security gate is weakened. Source authoring,
builds and partial passes qualify no unchecked task.

## Selected S3-08 adapters and corrections — 7214f46

Source `7214f4642280eae28e359423b627cd6265a400b8` freezes 1,067 tracked
inputs. Lint, formatting (526 files), strict typing (130 source files), all 12
recipe schemas and the unchanged 20-path public API schema pass. All five fresh
package builds and provenance checks pass. The full common profile stopped during
collection: `test_bounded_memory.py` imported nonexistent `ExecutionOwnership`
instead of the existing `ExecutionOwnershipStore`. Pytest reported one collection
error in 16.20 seconds; the driver ended with exit 2 after 27.72 seconds, with
no tracked source changes. No behavioral acceptance follows from collection.

Independent native profiles were stopped after observed failures once the grouped
corrections were ready: ADK and LangGraph each had 35 observed failures in named
role admission; optional Framework had one observed projection fixture failure.
These are progress observations, not final pytest totals. Explicit superseded
markers and result records preserve the source, command, stop reason and hashes.
No task checkbox changes.

## Corrected common fixture batch — caa32c8

The frozen common profile completed with 28 failures, 2,980 passes and 666 skips
in 1,145.42 pytest seconds (1,158.78 driver seconds), with unchanged 1,067 tracked
inputs. Failures are 25 role-boundary setup cases lacking immutable OpenAPI
binding fields, one nonexistent handle snapshot method, one older raw credential
option witness and one positive OpenAPI bearer fixture missing binding fields.
All have grouped fixture corrections authored separately; the actual initial
role archive admission defect is also corrected without loosening exact role
references. The common provider/composition modules recorded 80 passes, one
fixture failure and six actual PostgreSQL skips. New local memory and event
profiles passed their executed cases; required native, actual database and hosted
gates remain open. Source changes qualify no task or inventory row.

## Latest qualification source — 4740732

Frozen source `47407323776865704cda3bf5f48c6d9e5a91ea4e` retains 1,067
tracked-input hashes. The optional Agent Framework profile completed with
**767 passes, 354 skips and 2,934 deselections** in 1,637.99 pytest seconds
(1,661.19 driver seconds), exit 0. LangGraph completed with **73 failures,
1,146 passes, 672 skips and 1,467 deselections** in 3,865.85 pytest seconds
(3,895.09 driver seconds), exit 1. Neither completed end report records tracked
source changes or a timeout. Common and ADK are still running at this record;
their final reports and totals remain pending.

The originating `qualification-fixes` worktree retains each command, source
manifest, log and end report under `tmp/adapter-batch-4740732-*`. The optional
Framework log SHA-256 is
`2b79d2ec1679f1b46147fb8340ea5c97d025e0b1e70c2c535bbe959fa4724a68`;
the LangGraph log SHA-256 is
`de9775018b5a37898d07d7a44524e461f0969aac1eccce5ff156d5e100ee4576`.
Foreign native instances were deselected only in their independent environments;
actual PostgreSQL and other skipped gates are not inferred from local passes.

The separate `admission-renewal` checkout contains an authored bounded physical
artifact/SDK verification phase and five grouped fixture patches. Only copied
configuration and physical file verification move off-loop; database operations,
original trust checks and synchronous admission transactions stay on-loop.
Cancelled workers retain their bounded slots until completion. Fresh prepared
evidence belongs to one task and one synchronous phase, and changed provider or
deployment bindings reject before admission. The new source-level renewal witness
retains the original half-second lease and waits past its initial database-clock
expiry before validating renewed ownership; expiry/takeover and no-mutation
negatives remain required behavioral evidence.

Ruff and formatting pass across 527 files; strict typing passes across 134 source
files. The coherent correction source has not yet run its behavioral batch. These
static results and authored witnesses do not retroactively qualify `4740732`,
complete any task, change feature statuses or substitute for remaining native,
database, platform, image, security and hosted publication gates. Earlier results
remain preserved in the sections above.
