# S3-01 common MCP integration acceptance

Status: implementation and fixtures are authored; S3-01 remains unchecked until
the exact-source hosted qualification matrix passes. This record defines the
finite acceptance gates before capability or feature completion. It is not
evidence of successful official SDK interoperability or full MCP conformance.

## Contract and boundaries

Open Workflow 1.0.3 remains the public DSL and its schema remains unchanged.
Workflow calls and configured agent tools share the same common MCP service.
The selected modern wire profile is `2026-07-28`; unsupported versions and
interactions fail explicitly. The official Python SDK must be reviewed, pinned
and installed from each independent build lock, never during invocation or
sandbox startup. An SDK dependency alone does not qualify any transport.

For workflow operations that invoke an MCP method, successful workflow output
retains the validated JSON-RPC result, with absent fields distinct from
explicit nulls. For MCP calls used as configured agent tools, the SDK result is
projected to model content without JSON-RPC envelope fields. Method-specific
parameters, reserved metadata, routing, response identity and result models are
checked before a result becomes a known business receipt. Protocol errors and
tool errors use the common error contract. Input-required interactions cannot
be completed by fabricating a response or automatically replaying a business
call.

HTTP dispatch retains the common timeout, TLS, authentication, response-size,
host and redirect controls. Credential preparation and response consumption
share the operation deadline. A named per-tool identity includes its selected
TLS certificate and CA; it cannot silently inherit another tool's identity.
The finite OAuth client-credentials profile requires deployment-owned issuer,
resource and endpoint bindings. Unsupported discovery, interactive consent and
delegated grants remain separate capability gates.

The official Open Workflow 1.0.3 schema admits `transport.options` without
assigning it semantics, and the `client` object defines only `name` and
`version`. Keep that schema unchanged: reject `transport.options` and unknown
client fields such as `client.description` before destination authorization,
credential resolution, or transport I/O until a versioned contract defines
their behavior. They must not be silently ignored or interpreted by the SDK.

Stdio is disabled by default and requires the deployment-enabled internal
sandbox plus an exact registered executable, arguments and environment policy.
The common SandboxManager owns process creation, dedicated workspace, minimal
environment, descriptors, resource controls, bounded duplex framing and cleanup.
Adapters and SDK convenience launchers cannot create independent processes.
The internal process boundary is controlled execution, not hard isolation.
External sandbox duplex support is rejected before launch until independently
qualified. Capability claims describe only enforced platform controls.

Windows async SDK servers require an explicitly approved nonsecret `SYSTEMROOT`
environment binding to start the platform event loop. The restricted launcher
does not silently inherit the runtime environment. Windows file-size resource
limits remain unsupported; selecting that requirement rejects before launch.

Remote cancellation does not undo a tool action. Known receipts replay without
receiver dispatch; an interrupted ambiguous write remains pending uncertainty.
There is no automatic write retry, remote rollback or general exactly-once
guarantee. Durable keys, deadlines, principal and original-definition recovery
continue through the established common contracts.

## Required finite gates

| Gate | Required evidence |
| --- | --- |
| MCP01 | Reviewed exact SDK/lock provenance; modern version admission; unsupported versions reject before I/O; no automatic legacy negotiation. |
| MCP02 | Actual pinned SDK server interoperability for all seven OWS methods; method-specific params/meta/routing, prompts/resources/templates, null/empty content, cursors, correlated responses, errors and malformed results. |
| MCP03 | Actual bounded HTTP listener; supported content types and selected streaming behavior; timeout/cancellation, response limits, TLS/host/redirect policy and cleanup. |
| MCP04 | Static named bearer/API-key and actual per-tool mTLS isolation; bounded strict OAuth token handling, endpoint/resource binding, expiry, cancellation and sanitized failures; zero business dispatch on failed authentication. |
| MCP05 | Disabled-default and exact deployment stdio admission; actual SDK server through common duplex sandbox; UTF-8/JSON/ID/frame/queue/output limits, stalled or failed peers, Windows/POSIX descendant and workspace cleanup. |
| MCP06 | Unsupported input-required, notifications, Tasks and other optional interactions rejected explicitly without fabricated completion or business replay; supported subsets require their own evidence. |
| MCP07 | Actual ADK/LangGraph workflow and configured tool parity; SQLite/actual PostgreSQL known receipt replay, interrupted uncertain action, distinct visit keys and retained deadlines/identities across recovery. |
| MCP08 | Official schema unchanged; effective or explicitly rejected configuration fields, expression resolution, immutable fingerprint and approved endpoint/credential policies. |

After all changes and fixtures are authored, freeze sources for common security,
protocol and sandbox tests; both actual principal SDKs and relevant database
contracts; Windows/POSIX process controls; selected CTK; independent builds,
runtime images and complete security audits. Record source hashes, node counts,
skips, failures and exact hosted run identities. Optional Framework evidence
remains separate. Update only proven feature rows; proposed tests, JavaScript,
full OWS conformance and broader MCP extensions stay independently gated.

## Evidence

Initial read-only preparation identified actionable modern metadata/result
projection, OAuth transport, per-tool TLS and duplex sandbox gaps. Subsequent
correction batches authored those interfaces and controls; their full
qualification remains pending. There is no established blocker; missing
qualification is active work.

The authored MCP04 regression
`tests/core/test_mcp_sdk_http.py::test_actual_mcp_tls_keeps_configured_tool_certificates_isolated`
composes configured per-tool TLS profiles with actual SDK calls to two mTLS
listeners. It verifies that the selected certificates stay isolated and that
wrong or missing certificates are rejected before a second business dispatch.
The regression is present in the `b3d7b61` grouped local batch, which passed
151 tests with 68 skips. This is local witness evidence only; exact-source
qualification remains open. The latest `17501ee` matrix passed PostgreSQL and
four other hosted workflows but had one Windows independent-job recovery
failure. See the [latest exact-source outcome](validation-history/pending-batch-results.md#exact-source-validation-attempt-17501ee)
and the [preceding `b3d7b61` attempt](validation-history/pending-batch-results.md#exact-source-validation-attempt-b3d7b61).

The MCP08 review found that schema-admitted `transport.options` had no defined
semantics and was rejected only later in transport handling, while the
undeclared `client.description` could pass permissive schema handling and be
dropped by SDK model validation. A local correction now rejects both fields
during configured-tool validation and prepared-call admission, as well as
direct common MCP dispatch and the SDK boundary. It rejects before workflow
effect-intent creation, destination and credential authorization, credential
preparation, or I/O. Deterministic tests cover direct, prepared-call and
role-tool paths. The focused MCP HTTP,
protocol and tool-registry core suites pass locally: 138 tests passed, with
three existing HTTPX certificate-fixture deprecation warnings. This focused
result does not qualify MCP08 or the integrated S3-01 gates. The official schema
remains unchanged.

A further focused regression batch covers
legacy and unknown version rejection during regular and sandbox compilation;
exact parameters delivered to the SDK server across all seven methods; streamed
response-size overflow cleanup without replay; OAuth token expiry before
business dispatch; malformed UTF-8/JSON through the stdio bridge with sanitized
errors and cleanup; notification-only business replies as explicit uncertain
unsupported interactions; and configured-tool delivery of the validated result
without its JSON-RPC envelope. The combined local batch passed 151 tests, skipped
68 and reported three existing HTTPX certificate-fixture deprecation warnings.
It does not complete individual MCP gates; the frozen common,
principal-engine/database, process, build and exact-source hosted batch remains
required.

The first frozen local snapshot `f1e6abf` contains 416 fixed validation inputs.
Common focus reaches 222 passes with eight failures and two errors; both native
focus profiles reach their ten-failure limit on fixture setup. Five independent
wheel/sdist builds and provenance checks pass. Source hashes remain unchanged.
Original logs and supervisor records are retained in `tmp/s3-01-first-batch`.
These are partial failed attempts, not qualified interoperability.

Corrections target the complete HTTP/authentication deadline, distinction
between known pre-dispatch rejection and uncertain remote completion, bounded
Windows fixture IDs, actual TLS certificate wiring, valid negative backend
configuration, the native deployment-binding callback and stdio result/EOF
handling. Existing byte/time/security assertions remain. Author the complete
correction set before the next integrated behavioral batch.

The corrected focused snapshot `b20ade2` passes 256 common cases (four unrelated
native skips) except two test setup errors; both actual principal focused suites
pass 79 cases each, with 48 foreign/backend skips each. Five builds/provenance
checks pass. `325458a` corrects only those two setups, preserving runtime bytes.
The two affected cases then pass. The full frozen batch retains 416 inputs and
finishes unchanged: root 720 passed/492 skipped/eight failed, ADK 680/418/eight,
LangGraph 684/430/eight, optional Framework 827/575/eight. The eight-failure
limit ends these incomplete suites; they do not qualify the source. Records and
logs remain in `tmp/s3-01-third-batch`.

That batch exposes an obsolete audit symbol, an HTTP-only mock used as an MCP
reply, and child catalog replacement assertions superseded by original-definition
recovery. Corrections retain strict protocol correlation, original child source,
input, known outcome and no-second-dispatch assertions. An actual ADK hard stop
also leaves a recoverable SQLite attached-transaction rollback journal. Read-only
native preflight cannot recover the original database. A bounded helper now
inspects a stable private copy, validates and removes trusted master references
only in the copy, and lets SQLite recover that copy. Ordinary reads stay read-only;
unqualified formats, changed/corrupt files, WAL and unknown paths fail closed.
Original database/journal files remain untouched before lease acquisition. The
diagnostic over the exact failed database records original hashes and timestamps
unchanged; this is a reproduction, not completed behavioral qualification.
Thirteen dedicated controls and the original actual-process case require the
next corrected batch, followed by full independent and exact hosted gates.

The first inspection correction `e2fcb3b` passes five builds but exposes a
Windows metadata API mismatch in its fixture batch: path stat reports creation
time while descriptor stat reports change time. `f732247` compares each API
against itself and retains supported cross-API identity/size/mtime checks. All
13 inspection controls pass, as does the original ADK `partial-all` actual
hard-stop case. The frozen broader correction retains 418 inputs unchanged:
root 169 passed/65 skipped/four failed, ADK 174/154/four, LangGraph 174/154/four,
optional Framework 194/212/six. Only an overly specific newly added pending-child
error assertion fails; the runtime correctly preserves the parent's unresolved
intent without another receiver call. Its correction also checks the retained
pending receipt. Logs, bounds, hashes and five fresh passing builds remain in
`tmp/s3-01-fourth-batch` and `tmp/s3-01-fifth-batch` respectively.

The final authored helper rejects forged master/member paths lexically before
filesystem access, including UNC references. Its additional control brings the
inspection grid to 14; this hardening awaits the combined final validation.
S2-09, S3-01 and the separately authored S3-02 will share one final frozen source,
full validation and hosted publication checkpoint. No task is complete from
these partial or failed attempts.
