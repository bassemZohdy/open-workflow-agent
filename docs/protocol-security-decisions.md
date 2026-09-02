# Protocol and Security Architecture Decisions

Date: 2026-08-28

This document records the project-wide protocol, security, A2A, traffic-policy, and sandbox-contract decisions. `Project Definition.md` remains the product/architecture authority; this file is the focused implementation policy.

## 1. Protocol version policy

OWA targets the **latest stable released** version of every external protocol/specification it implements or advertises. Draft, preview, RC, editor-draft, or unreleased revisions are not production contracts.

Each baseline change follows:

```text
authoritative stable release
  -> compatibility/security review
  -> exact pinned baseline
  -> implementation/migration
  -> deterministic tests
  -> capability advertisement
  -> release
```

### No backward compatibility before contract stabilization

OWA is still pre-stable. It does not carry legacy protocol generations by default.

When moving to a newer stable baseline:

- remove legacy version-specific semantics and aliases unless a new explicit product decision requires them;
- do not preserve v0.x behavior only because an SDK supports it;
- do not advertise multiple generations without dedicated compatibility tests.

A2A v0.3 compatibility is therefore intentionally removed rather than preserved.

## 2. Pinned baselines

The verified record is maintained in [protocol-baselines.md](protocol-baselines.md).

```text
Open Workflow Specification  1.0.3
A2A Protocol                 1.0.1
Model Context Protocol       2026-07-28
OpenAPI Specification        3.2.0
CloudEvents                  1.0.2
AsyncAPI Specification       3.1.0
```

gRPC is infrastructure/tooling unless an OWA feature introduces a separately versioned application binding.

`/v1/capabilities` and protocol discovery metadata advertise only implemented, verified behavior. Prefer `<Protocol> <version> bounded profile` until conformance/interoperability evidence justifies a broader claim.

## 3. Security is deployment configuration

Authentication and authorization are runtime/deployment concerns, configured through strict YAML plus the existing `OWA__...` environment override convention. Protocol adapters consume resolved security policy; they do not own deployment identity policy.

Workflows must never contain raw credentials.

## 4. Supported security profile types

The initial shared security model intentionally supports only the common interoperable mechanisms:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Do not add uncommon, legacy, or vendor-specific mechanisms without a demonstrated requirement.

Conceptual target schema:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        from_env: A2A_PARTNER_TOKEN

    partner-api:
      type: api_key
      location: header
      name: X-API-Key
      value:
        from_env: PARTNER_API_KEY

    internal-service:
      type: oauth2_client_credentials
      token_endpoint: https://identity.example.com/oauth2/token
      client_id:
        from_env: INTERNAL_CLIENT_ID
      client_secret:
        from_env: INTERNAL_CLIENT_SECRET
      scopes: [workflow.invoke]

    internal-mtls:
      type: mtls
      certificate:
        from_env: OWA_CLIENT_CERT_PATH
      private_key:
        from_env: OWA_CLIENT_KEY_PATH
      ca_certificate:
        from_env: OWA_CA_CERT_PATH
```

These examples describe the target model. The `bearer` profile shape (`type`, `token.from_env`) is implemented and consumed by A2A inbound authentication today; the `api_key`/`oauth2_client_credentials`/`mtls` field names above are illustrative only and do not yet match the strict parser (see `core/src/open_workflow_agent/security.py` for the authoritative current fields). They become authoritative once a real adapter consumes them and `SECURITY-1` through `SECURITY-4` are fully green.

## 5. Authorization vocabulary

Use these terms consistently:

- **principal / identity** — authenticated caller, service, or agent;
- **role** — named grouping of permissions;
- **scope** — credential/delegation authority, especially OAuth2;
- **permission / action** — concrete allowed operation;
- **resource** — target object, service, skill, workflow, or API;
- **audience** — intended token/service recipient.

Roles and scopes are not synonyms.

Use protocol-native action identifiers where available. For A2A examples:

```text
message.send
tasks.get
tasks.cancel
```

The implemented A2A binding enforces these through `a2a.authorization`, an
explicit deployment-owned allow policy evaluated against the authenticated
principal of `a2a.security_profile`. Actions use the vocabulary above;
resources are `skill:<id>` for SendMessage (the client-selected deployment
skill, or `skill:workflow` for the implicit single-workflow skill) and the
`tasks` collection for Task operations. First matching rule allows; no match
denies with a sanitized 403. Card discovery is authentication-gated but not
policy-gated because clients need the card to discover the interface.
Per-task resources would require per-caller task ownership and remain out of
scope with multi-tenancy (section 12). Declaring a policy without a security
profile is rejected at startup — authorization without authenticated
principals is not supported.

## 6. Named reusable profiles

Protocol integrations reference named security profiles instead of duplicating credentials:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        from_env: A2A_PARTNER_TOKEN
      authorization:
        principal: partner-agent
        roles: [partner]
        scopes: [agent.invoke]
        permissions:
          - action: message.send
            resources: [skill:residence-renewal]
          - action: tasks.get
            resources: [skill:residence-renewal]
        audience: open-workflow-agent

a2a:
  security_profile: partner-agent
```

The previous single A2A bearer field (`auth_token`) was a temporary implementation detail, not a compatibility contract. It has been removed now that the shared profile model is wired for A2A inbound authentication; `a2a.security_profile` must reference a `bearer`-type entry in `security.profiles`. Nested `authorization` (roles/scopes/permissions enforcement) shown above remains conceptual and is not yet parsed or enforced (`SECURITY-4`).

## 7. Secret handling

Sensitive values come from deployment-owned secret/environment mechanisms such as Kubernetes/OpenShift Secrets, Docker secrets, Vault, External Secrets Operator, or equivalent secret managers.

Secrets must never be serialized into workflows, plans, capability responses, Agent Cards, lifecycle events, task/artifact payloads, logs, sandbox output, or persisted invocation metadata.

## 8. Enterprise identity boundary

OWA may enforce bounded authentication/authorization but **must not become an identity provider**.

OAuth2/OIDC federation, token exchange, delegated-user identity, consent, and cross-domain identity remain deployment/identity-platform responsibilities. Delegated user identity is deferred until a concrete enterprise A2A/MCP requirement exists, and must use standards-based identity mechanisms when introduced.

## 9. Traffic policy is separate

Rate limiting, concurrency limits, request/burst controls, circuit behavior, and related admission policy belong to a separate deployment-controlled `traffic_policy` model.

```yaml
traffic_policy:
  inbound:
    max_concurrent_requests: 100
    requests_per_second: 50
    burst: 100
```

Security answers **who is the caller and what may it do?** Traffic policy answers **how much traffic may be admitted?** Do not merge these models.

## 10. A2A skill ownership

A2A supports multiple deployment-configured skills backed by explicitly registered workflows:

```yaml
a2a:
  skills:
    - id: residence-renewal
      name: Residence Renewal
      workflow: residence-renewal
    - id: residence-status
      name: Residence Status
      workflow: residence-status
```

Routing is deployment-owned. An A2A client must never select an arbitrary workflow path, file, catalog entry, or execution backend.

## 11. A2A Tasks

A2A Tasks are a protocol projection over common OWA invocation state, not a second execution engine.

Preferred identity relationship:

```text
A2A task_id == OWA invocation_id
```

unless the pinned A2A specification requires a distinct external identifier. Common invocation, persistence, resume, cancellation, approval, and lifecycle services remain authoritative.

Implementation order:

```text
A2A 1.0.1 Agent Card / SendMessage
  -> Task projection
  -> Task get/cancel
  -> waiting/input-required + resume
  -> spec-native async behavior
  -> message/task streaming
  -> resubscription/interoperability gates
```

Do not invent proprietary async/stream extensions when A2A defines native semantics.

Waiting, input-required, and resume mapping are protocol projections of the common invocation/resume contracts, never a second resume mechanism. A blocking `SendMessage` returns `result.task` when the workflow ends up waiting (`TASK_STATE_INPUT_REQUIRED`); `SendMessageConfiguration.returnImmediately` is the only asynchronous flag OWA implements, and it returns the Task projection immediately for later `GetTask` polling. A resuming send carries the existing `message.taskId` and reuses the common resume contract (fingerprint-verified against the original workflow); unknown tasks return `task_not_found`, and tasks that are not waiting are rejected with a sanitized `task is not accepting input` error.

Push notifications remain deferred because they introduce a separate outbound callback trust boundary: allowlisting, SSRF controls, TLS identity, callback authentication, replay/idempotency protection, bounded retries/dead-letter handling, and secret-safe observability.

## 12. Multi-tenancy

Multi-tenancy is outside the current product scope. New structures should avoid obvious future blockers, but no tenant model, tenant routing, or tenant-aware authorization is implemented now.

## 13. Sandbox contract naming

The naming cleanup is **implemented**.

```text
sandbox/contract.py
```

owns backend request/result/interface contracts.

```text
sandbox_capabilities.py
```

owns portable execution requirements, backend capability descriptors, and compatibility checks.

The two responsibilities remain intentionally separate.

## 14. Required verification

Before expanding protocol/security capability advertisement, applicable tests must cover:

- strict YAML/environment configuration parsing;
- secret-reference resolution and non-disclosure;
- authentication success/failure;
- authorization allow/deny by principal, scope, action, and resource;
- protocol-version metadata accuracy;
- malformed/oversized request rejection;
- sanitized errors;
- timeout/cancellation behavior;
- persistence/restart behavior for durable state;
- cross-engine observable parity;
- authoritative interoperability/conformance fixtures where available.

Shared contract tests remain the portability proof.
