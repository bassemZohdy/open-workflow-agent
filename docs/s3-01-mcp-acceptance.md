# S3-01 common MCP integration acceptance

Status: implementation in progress; S3-01 remains unchecked. This record defines
the finite acceptance gates before capability or feature completion. It is not
evidence of successful official SDK interoperability or full MCP conformance.

## Contract and boundaries

Open Workflow 1.0.3 remains the public DSL and its schema remains unchanged.
Workflow calls and configured agent tools share the same common MCP service.
The selected modern wire profile is `2026-07-28`; unsupported versions and
interactions fail explicitly. The official Python SDK must be reviewed, pinned
and installed from each independent build lock, never during invocation or
sandbox startup. An SDK dependency alone does not qualify any transport.

Successful workflow MCP output is the validated JSON-RPC result, with absent
fields distinct from explicit nulls. Method-specific parameters, reserved
metadata, routing, response identity and result models are checked before a
result becomes a known business receipt. Protocol errors and tool errors use
the common error contract. Input-required interactions cannot be completed by
fabricating a response or automatically replaying a business call.

HTTP dispatch retains the common timeout, TLS, authentication, response-size,
host and redirect controls. Credential preparation and response consumption
share the operation deadline. A named per-tool identity includes its selected
TLS certificate and CA; it cannot silently inherit another tool's identity.
The finite OAuth client-credentials profile requires deployment-owned issuer,
resource and endpoint bindings. Unsupported discovery, interactive consent and
delegated grants remain separate capability gates.

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

Read-only preparation identified actionable modern metadata/result projection,
OAuth transport, per-tool TLS and duplex sandbox gaps. Implementation is being
authored in parallel while the separate S2-09 frozen batch is validated. There
is no established blocker; missing interfaces are implementation work.

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
