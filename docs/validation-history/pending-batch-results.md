# Pending batch results — 2026-10-13

These attempts do not close S2-09 or S3-01 through S3-05. Passing subsets and fresh
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
