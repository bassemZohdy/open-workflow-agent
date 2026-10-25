# S3-04 executable portability: authored acceptance plan

## Exact-source qualification d72781f

S3-04 is verified for its finite profile at `d72781f86053a7fd77e7b9df7c3a74c262923136`. Finite Python captured-source registry/admission/recovery and common awaited Python/JavaScript/shell execution, return projections, process controls, source identity and existing selected external sandbox boundaries.

Background activation remains S3-03; captured-source/background JavaScript, wider container policies, unsupported platform controls and independent JS workflow parity remain excluded.

All six source-matched workflows pass; [the retained results](validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f) include actual principal SDKs, both databases, platform/process, image, security and packaging checks. This is not full OWS/proposed-test conformance.

## Historical progress and acceptance design

The notes below predate the qualification above; their prior pending/failed states are preserved.

## JavaScript portability candidate — 2026-10-25

The missing JavaScript runtime now has an awaited candidate through the existing
common sandbox backend. It retains deployment opt-in, explicit approved binary
resolution, private `.cjs` source, UTF-8 arguments/stdin, filtered environment and
all existing process controls. Physical interpreter bytes join the enabled
profile's recovery identity. Background and captured-source JavaScript remain
unsupported. Digest-pinned Node 24 is copied into each runtime image at build
time; there is no startup/package installation or engine-specific process path.

The initial Windows physical probe identified Node's explicit `SYSTEMROOT`
requirement. Its deployment test policy now allows only that additional host
variable. The physical/control and existing sandbox/configuration regression
selection passes 153 cases, with four inapplicable POSIX skips; three CI selection
guards also pass. Actual ADK and
LangGraph SQLite return/projection and changed-interpreter recovery checks pass
two cases each; ADK PostgreSQL passes the same two. They retain actual native
snapshots and unchanged common state after drift rejection. Linux PostgreSQL,
packaged images and exact-source hosted acceptance remain required. The
[runtime constraints](sandbox-execution.md#script-execution) include the selected
V8 memory policy and Windows event-loop distinction. This candidate does not
close S3-04 or qualify independent JavaScript workflow execution under S4-03.

The frozen `a6a95cd` ADK and LangGraph images both pass a real native JavaScript
invocation with read-only root, no network, arbitrary UID, Docker memory/CPU/PID
bounds, filtered ambient credentials/options, UTF-8 input/arguments, exact public
result and removed execution workspace. LangGraph's first build failed a PyPI
connection timeout; its unchanged-source retry and packaged execution pass.
All five runtime package wheel/source builds pass; recovery provenance and 257
actual source/resource payload comparisons pass. This image batch predates the
later test/CI-only corrections and is not a whole-source acceptance checkpoint.

The new forced-stop witness passes four Windows cases per ADK/SQLite,
LangGraph/SQLite and ADK/PostgreSQL profile: five return projections in one real
native invocation, known/uncertain JavaScript producer stops, and physical
interpreter-drift rejection. Separate recovery processes retain original source,
input, identity, receipt digest, clock and native reference after natural lease
expiry. A non-deduplicating, physically flushed business log stays at one commit.
The unknown case never launches the operation again. Live child OS handles are
used only for test cleanup after the producer stop; POSIX orphan isolation is not
claimed. Earlier fixture failures used the wrong native-snapshot representation;
the final checks require actual invocation-scoped native row counts and hashes.
Linux SQLite/PostgreSQL and full hosted qualification remain required.

Portable `SandboxInputFile` requests now validate path syntax under both POSIX
and Windows grammars. Drive-qualified paths, UNC paths and backslash traversal
are rejected before a request reaches a different platform. Windows device
names, alternate data stream syntax, invalid characters and trailing-dot/space
aliases are rejected as well, including when nested below a directory. Focused
regressions cover these forms; the full cross-platform and deployment
acceptance gates remain unchanged.

Sandbox requests now also reject two staged files whose destinations collapse
to one path under portable filesystem rules: case-folded names, Unicode
canonical equivalents, and redundant POSIX separators. This prevents backend
staging from silently replacing one supplied file with another on
case-insensitive or Unicode-normalizing filesystems. The request-level contract
remains framework-neutral and does not expand runtime capability claims.

Status: source authored, unqualified. S3-04 remains unchecked. Registry
configuration/execution errors now propagate without being mislabeled as source
policy rejection. Only the dedicated expected source-policy exception is
translated; unrelated common unsupported-feature errors propagate unchanged.
New regression cases are authored and await the final batch.
The Windows CI source run `0d812b2` hit the eight-second wall deadline before
the one-second Job CPU quota fired; the fixture now gives that quota 20 seconds
with a 25-second outer bound. Exact-source hosted requalification remains
pending.
The same source's Linux root suite had one stdio child-start failure. The
public error remains sanitized; a follow-up records only a fixed OS error
category in runtime diagnostics so a recurrence can distinguish resource
exhaustion from an unavailable executable or permission denial. The safe-log
regression awaits the consolidated batch.
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
Reserved runtime environment names are rejected case-insensitively across
platforms, including `PATH`, `HOME`, `TMPDIR`, `TEMP`, and `TMP`; child temporary
directory variables are bound to the execution workspace. Actual-process and
admission regressions cover lowercase Windows spellings. Stdio startup errno
categories are logged only around process creation, while later OS failures
retain a sanitized operation error.
Docker and Kubernetes remain deployment-selected and subject to existing gates;
this batch does not replace real-cluster DEPLOY-1 acceptance.

Authored inventories are not passing counts: registry 114 cases, compiler/archive
30, imported-source rejection 24, independent source admission 114 across SQLite
and PostgreSQL, awaited process controls 27, shared actual native/source calls 28
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
