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
