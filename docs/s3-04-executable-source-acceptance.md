# S3-04 executable portability: authored acceptance plan

Portable `SandboxInputFile` requests now validate path syntax under both POSIX
and Windows grammars. Drive-qualified paths, UNC paths and backslash traversal
are rejected before a request reaches a different platform. Focused regressions
cover these forms; the full cross-platform and deployment acceptance gates
remain unchanged.

Status: source authored, unqualified. S3-04 remains unchecked. Registry
configuration/execution errors now propagate without being mislabeled as source
policy rejection. Only the dedicated expected source-policy exception is
translated; unrelated common unsupported-feature errors propagate unchanged.
New regression cases are authored and await the final batch.
No new executable operation, platform, backend or hard-isolation capability is
advertised by this record. S3-03 corrections must be integrated before freezing
this batch.

The public Open Workflow 1.0.3 document keeps its original `run.script.source`
endpoint and canonical task references. Deployment configuration can supply a
finite immutable registry of approved literal HTTPS identifiers and exact UTF-8
Python bytes, with mandatory SHA-256, source ID and version. The registry permits
at most 32 captures, 1 MiB per source and 16 MiB in total. The identifier performs
no HTTP fetch, filesystem read, import or dependency installation. Dynamic,
credential-bearing and unknown endpoints reject admission. Workflow-owned direct,
nested and local reusable function scripts are the finite authored scope;
imported catalog executable-source functions reject before normalization.

The derived private plan materializes verified code while preserving the public
document. Captures contribute to its fingerprint and common immutable definition
archive. Empty-capture legacy plans retain their prior fingerprint/archive shape.
Each actual source call additionally needs an independent source-admission row,
index and completed policy receipt bound to its original invocation, input,
principal, original clock and request digest. A business-only native replay must
still verify that proof. Missing or altered ephemeral source metadata rejects
against the original admitted task before a business intent. Changed registry,
deleted archive/policy/index and damaged original input cannot silently adopt a
replacement source or invoke a process. Background source requests use the same
original normalized request/input digest as the common job acceptance receipt.

Every executable call remains on SandboxManager. Awaited internal processes use
owned Windows Jobs before suspended-process resume, or supported POSIX limits and
process groups. Unsupported requested limits reject before workspace/process
creation. Normal leader exit closes owned descendants before inherited pipes can
hold output draining open; cancellation, shutdown and original wall deadline keep
their existing common outcomes. Workspace/FD/environment/output controls remain
a controlled execution boundary, not container, pod, VM or microVM isolation.
Docker and Kubernetes remain deployment-selected and subject to existing gates;
this batch does not replace real-cluster DEPLOY-1 acceptance.

Authored inventories are not passing counts: registry 114 cases, compiler/archive
30, imported-source rejection 24, independent source admission 114 across SQLite
and PostgreSQL, awaited process controls 21, shared actual native/source calls 28
and process-stop/recovery 24 across the two principal engines and both databases.
The compiler/archive group includes a new regression that ensures unexpected
registry implementation failures are not mislabeled as workflow source-policy
rejections. The output-receipt bound gate now translates only its dedicated
finite-profile-limit error to an unsupported-feature rejection; unexpected
implementation errors propagate unchanged. Regressions for both branches are
authored and remain unrun with the rest of this batch.
The four actual-native/backend late-worker takeover cases are authored in
`tests/contract/test_executable_source_calls.py`; each requires the source
business receipt to remain pending after a successor acquires ownership. Their
SQLite/PostgreSQL × ADK/LangGraph exact-source qualification remains pending.
Actual fixtures require no source HTTP fetch, no paid model call, a non-deduplicating
SQLite business ledger and real separate process IDs. Known receipts must replay
without another call; a commit without receipt remains pending without resend.
Producer ownership expires naturally under the original 30-second lease and the
same original 120-second workflow clock. Native checkpoint evidence is genuine.

Required qualification includes one unchanged-source batch, full relevant suites,
actual principal SQLite/PostgreSQL checkpoints, Windows/POSIX process controls,
fresh independent package provenance, image gates, audits, source-linked hosted
reports and inventory/feature tracking. Source review and authored counts do not
close any of these gates. Failures will remain recorded with exact source,
commands, exit status and unchanged-source evidence.
