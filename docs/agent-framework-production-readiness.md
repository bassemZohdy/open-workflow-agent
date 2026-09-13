# Microsoft Agent Framework Production-Readiness Plan

Status: deferred readiness plan. This document does not advertise Microsoft
Agent Framework as a production OWA engine.

## Current support boundary

The adapter in `engines/agent-framework/` is optional and CI/evaluation
covered. It uses the existing OWA `WorkflowEngine` SPI and common executor,
keeps its dependency lock isolated, and does not create a production runtime
image. ADK and LangGraph remain the production-accepted engines.

The adapter must not introduce a second public workflow DSL, a third model or
tool configuration contract, public framework-native checkpoint identifiers,
or an engine-owned sandbox path.

## Activation condition

This plan becomes an active implementation milestone only when the project
explicitly decides that Microsoft Agent Framework is a production-supported
engine. That decision should identify the intended users, support window,
deployment environments, compatibility promise, and rollback owner.

## Best-practice baseline

### Package and dependency isolation

- Keep `agent-framework` dependencies in the independent adapter package and
  lock file.
- Prefer the smallest stable workflow packages; do not pull unrelated provider
  or Azure integrations into the runtime image.
- Keep model/provider selection in OWA's common contracts and optional extras.
- Build a separate image only after dependency size, license, vulnerability,
  and startup behavior are measured.

### Semantic and public-contract boundaries

- Compile the immutable common `WorkflowPlan`; do not reinterpret Open
  Workflow 1.0.3 in the adapter.
- Preserve common invocation identity, lifecycle events, cancellation, and
  sanitized errors.
- Keep framework-native workflow, executor, run, and checkpoint identifiers
  private to the adapter.
- Route executable operations through the common `SandboxManager`.
- Advertise only capabilities proven by deterministic tests and deployment
  acceptance.

### Durability and resume

- Use Agent Framework checkpoint storage only behind the adapter boundary.
- Treat checkpoint storage as trusted private infrastructure with least-
  privilege access; never restore checkpoints from untrusted input.
- Use stable logical executor identities when rebuilding a workflow so a
  checkpoint can be rehydrated after restart.
- Restrict serialized checkpoint types and never place credentials, provider
  tokens, or unbounded user data in checkpoint state.
- Prove interruption, restart, resume, cancellation, and side-effect replay
  behavior. Checkpoints do not imply exactly-once effects; callers still need
  idempotency.

The upstream guidance covers checkpoint storage, rehydration, stable executor
identity, and restricted deserialization in [Agent Framework checkpoint
documentation](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints).

### Verification and release gates

Before production advertisement, record green evidence for:

1. native workflow execution, cancellation, and common capability reporting;
2. checkpoint creation, secure storage, restart, rehydration, and resume;
3. common contract, persistence, and Portable Profile CTK behavior;
4. stable operation identity and idempotent side-effect replay behavior;
5. dependency isolation, image size, hardened image, and runtime acceptance;
6. vulnerability scanning, OCI SBOM, provenance, and release attestation;
7. documentation, rollback digest, and independent release metadata.

Until every applicable gate is green, the adapter remains optional
CI/evaluation support and its capability surface remains non-production.

## Definition of done

The plan is complete only when the explicit production-support decision is
recorded in `Project Definition.md` and `PROJECT.md`, all gates above are
verified on an exact commit, a separately tagged image is published, and the
support boundary is reflected in the README, troubleshooting, deployment, and
release documentation.
