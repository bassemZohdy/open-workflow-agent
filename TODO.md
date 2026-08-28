# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file tracks only active or intentionally deferred work. Completed implementation detail belongs in `PROJECT.md` and git history.

## Current Phase

**v0.1.0 released; bounded inbound A2A shipped; everything else intentionally deferred (2026-08-28).**

All actionable backlog work is complete: the full backlog sweep (documentation, supply-chain, pipeline hardening, quality) landed and proved green in CI, v0.1.0 was released from a verified head, the Kubernetes sandbox backend passed real-cluster acceptance on kind (Kubernetes 1.37, Calico enforcement), and a bounded inbound A2A profile — Agent Card plus synchronous `message/send` with selectable `jsonrpc`/`http_json` transports, defaulting to JSON-RPC as the most deployed — was implemented per the product decision of 2026-08-28, disabled by default and guarded by deterministic tests. Everything below is deferred by explicit decision, not by accident.

### Current integration head

- `main` is aligned with `origin/main`. The A2A slice's remote CI verification runs with its push; record the resulting run IDs in `PROJECT.md` after they complete.
- Local verification: root `280 passed, 11 skipped` at ≥80% coverage, ADK `104 passed`, LangGraph `104 passed`, Agent Framework `144 passed` across native + shared contract + CTK surfaces, all six locks checked, all seven packages built.
- The current release remains **v0.1.0** (`c47cb86`); green `main` commits keep publishing `latest` and `sha-*` images, and future releases follow the same tag-on-green flow (R-001, closed).

## Active Backlog

None — the ordered backlog is empty. See the intentionally deferred work below.

## Intentionally Deferred

### OpenShift sandbox acceptance (deferred 2026-08-28)

Kubernetes acceptance is recorded green (see `PROJECT.md`). OpenShift-specific validation is intentionally skipped for now:

- SCC/security-context/arbitrary-UID behavior on OpenShift;
- the same lifecycle/security cases under OpenShift;
- OpenShift-specific advertisement through `/v1/capabilities` (the sandbox `platform` field already distinguishes `openshift`; it must not be advertised for OpenShift deployments until this acceptance runs).

Revisit when an OpenShift cluster is available; the kind acceptance procedure in `docs/development.md` is the template.

### A2A conformance, streaming, and push notifications (deferred 2026-08-28)

The bounded inbound A2A profile is implemented (Agent Card + synchronous `message/send`, selectable transports, optional bearer auth, message bounds, sanitized errors). Deliberately out of scope until a future decision:

- `message/stream` and general portable output streaming beyond bounded lifecycle SSE (pre-conditions documented in `docs/a2a-streaming-evaluation.md`);
- push notifications (callback allowlisting, TLS/server identity, replay/idempotency policy);
- persistent task objects, full Agent Card conformance, and any broader A2A conformance claim;
- additional transport implementations beyond `jsonrpc` and `http_json` (the configuration flag and transport registry accept new implementations without workflow changes).

### Microsoft Agent Framework production status (deferred 2026-08-28)

The adapter remains an optional package with CI-enforced native, shared contract, and CTK coverage (144 tests green). Deferred by decision: the production-engine work — independent runtime image, hardened-image acceptance, persistence/resume coverage, capability reporting, and release metadata. The evaluation record lives in `docs/engine-adapter-evaluation.md`.

## Working Rules

- Add or update tests before marking backlog items complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Route executable workflow operations through the common `SandboxManager`; engines must never create independent subprocess/Docker/Kubernetes execution paths.
- Treat the internal sandbox as a controlled execution boundary, not a hard isolation boundary; advertise only controls actually enforced by the selected backend/platform.
- Do not give the main runtime unrestricted Docker socket or cluster-wide Kubernetes/OpenShift access.
- Keep backend selection deployment-controlled; workflow definitions must not choose infrastructure backends directly.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, schedule, sandbox execution, and engine-native state lifecycles.
- Do not require paid model/API access or install dependencies at container startup/runtime execution.
- Keep production capabilities fail-closed until deterministic contract tests and the relevant release/deployment acceptance gates are green.
