# Protocol and Security Architecture Decisions

Date: 2026-08-28

This document records the project-wide decisions for protocol baselines, security configuration, A2A skill/task semantics, traffic policy, and the sandbox contract naming cleanup.

`Project Definition.md` remains authoritative. This document refines how protocol integrations and security configuration must be implemented.

## 1. External protocol version policy

Open Workflow Agent targets the **latest stable released version** of every external protocol/specification it implements or advertises.

Each OWA release pins an explicitly reviewed baseline. Draft, preview, RC, editor-draft, or unreleased revisions are not production contracts.

Required process:

```text
latest stable release from authoritative source
        ↓
compatibility/security review
        ↓
pin exact project baseline/schema/fixtures
        ↓
implementation/migration
        ↓
deterministic contract/conformance/interoperability tests
        ↓
capability advertisement
        ↓
release
```

### No backward compatibility at this stage

OWA does **not** carry legacy protocol generations while its public product contract is still stabilizing.

When migrating an implementation to a newer stable protocol baseline:

- remove legacy version-specific semantics and discovery aliases unless a new explicit product decision requires them;
- do not maintain v0.x compatibility merely because an SDK supports it;
- do not advertise multiple protocol generations without dedicated tests and a later compatibility decision.

This specifically means the A2A v1 migration will not retain v0.3 compatibility behavior.

## 2. Pinned protocol baselines

The verified baseline record is maintained in `docs/protocol-baselines.md`.

Current baselines:

```text
Open Workflow Specification  1.0.3
A2A Protocol                 1.0.1
Model Context Protocol       2026-07-28
OpenAPI Specification        3.2.0
CloudEvents                  1.0.2
AsyncAPI Specification       3.1.0
```

gRPC is treated as transport/tooling infrastructure unless an OWA feature introduces a separately versioned application protocol binding.

## 3. Capability advertisement

`/v1/capabilities` and protocol-specific discovery metadata must advertise only protocol/version/features that are implemented and deterministically verified.

Preferred wording remains:

```text
<Protocol> <version> bounded profile
```

until the applicable conformance/interoperability suite justifies a broader claim.

## 4. Authentication and authorization are deployment configuration

Authentication and authorization are runtime/deployment concerns and must be externally configurable.

Configuration precedence remains:

```text
built-in defaults
      ↓
YAML configuration
      ↓
environment-variable overrides
```

Environment-variable overrides use the existing `OWA__...` nested convention.

Protocol implementations consume resolved security configuration; they do not own deployment security policy.

## 5. Supported security profile types

The initial shared security schema intentionally supports only the most common interoperable mechanisms:

1. `bearer`
2. `api_key`
3. `oauth2_client_credentials`
4. `mtls`

Do not add uncommon, legacy, or vendor-specific authentication mechanisms without a demonstrated requirement.

Conceptual examples:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        fromEnv: A2A_PARTNER_TOKEN

    partner-api:
      type: api_key
      in: header
      name: X-API-Key
      value:
        fromEnv: PARTNER_API_KEY

    internal-service:
      type: oauth2_client_credentials
      token_endpoint: https://identity.example.com/oauth2/token
      client_id:
        fromEnv: INTERNAL_CLIENT_ID
      client_secret:
        fromEnv: INTERNAL_CLIENT_SECRET
      scopes:
        - workflow.invoke

    internal-mtls:
      type: mtls
      certificate:
        fromEnv: OWA_CLIENT_CERT_PATH
      private_key:
        fromEnv: OWA_CLIENT_KEY_PATH
      ca_certificate:
        fromEnv: OWA_CA_CERT_PATH
```

Exact field names are finalized by the strict Pydantic implementation, but these four mechanisms define the initial scope.

## 6. Standard authorization vocabulary

OWA configuration and documentation use the following standard terms consistently:

- **principal / identity** — authenticated caller/service/agent;
- **role** — named grouping of permissions where role-based authorization is useful;
- **scope** — delegated or credential-associated authority, especially OAuth2;
- **permission / action** — concrete operation the principal may perform;
- **resource** — object/service/skill/workflow the action targets;
- **audience** — intended token/service recipient where applicable.

Protocol-native action names should be used when a protocol defines them. A2A examples include:

```text
message.send
tasks.get
tasks.cancel
```

Roles and scopes are not synonyms. A deployment may map roles to permissions or scopes, but the runtime configuration model must keep the concepts explicit rather than silently treating one as the other.

## 7. Named security profiles

Protocol integrations reference reusable named security profiles instead of duplicating credentials in each protocol block.

Conceptual configuration:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        fromEnv: A2A_PARTNER_TOKEN
      authorization:
        principal: partner-agent
        roles:
          - partner
        scopes:
          - agent.invoke
        permissions:
          - action: message.send
            resources:
              - skill:residence-renewal
          - action: tasks.get
            resources:
              - skill:residence-renewal
```

Protocol configuration then references the profile:

```yaml
a2a:
  security_profile: partner-agent

mcp:
  servers:
    customer-data:
      url: https://mcp.example.com
      security_profile: internal-service
```

Workflows may reference a configured security profile only where the runtime contract allows it; they must not carry raw credentials.

## 8. Secret handling

Preferred pattern:

```yaml
token:
  fromEnv: A2A_PARTNER_TOKEN
```

The real secret should be provided by Kubernetes/OpenShift Secrets, Docker secret/environment injection, Vault, External Secrets Operator, or an equivalent deployment secret manager.

Secrets must never be serialized into:

- workflow definitions;
- execution plans;
- capability responses;
- Agent Cards;
- lifecycle events;
- task/artifact payloads;
- logs;
- retained sandbox output;
- persisted invocation metadata.

## 9. Enterprise identity boundary

OWA may enforce bounded local authentication and authorization, but **must not become an identity provider**.

Enterprise OAuth2/OIDC federation, token exchange, user delegation, consent, and cross-domain identity remain deployment/identity-platform responsibilities.

Delegated user identity is intentionally deferred until a concrete enterprise A2A/MCP requirement exists. When introduced it must use standards-based identity/token-exchange mechanisms rather than custom message fields.

## 10. Traffic policy is separate from security profiles

Rate limiting, concurrency limiting, request/burst control, circuit behavior, and similar traffic-management concerns belong in a separate deployment-controlled `traffic_policy` model.

Conceptually:

```yaml
traffic_policy:
  inbound:
    max_concurrent_requests: 100
    requests_per_second: 50
    burst: 100
```

A security profile answers **who is this caller and what may it do?**

A traffic policy answers **how much traffic may be admitted and under what operational limits?**

Do not mix the two models.

## 11. A2A skill ownership

The A2A runtime supports multiple deployment-configured skills backed by explicitly registered workflows.

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

Routing is deployment-owned:

```text
A2A skill id
     ↓
configured mapping
     ↓
registered workflow
```

An A2A client must never choose an arbitrary workflow path, file, catalog entry, or execution backend.

## 12. A2A Task model

A2A Tasks are a protocol projection over common OWA invocation state, not another execution engine.

Preferred identity relationship:

```text
A2A task_id == OWA invocation_id
```

unless A2A `1.0.1` requires a distinct external identifier.

The exact state mapping is validated against the pinned A2A specification during implementation. Common OWA invocation/persistence/resume/cancellation/approval/lifecycle services remain authoritative.

## 13. A2A synchronous, asynchronous, and streaming behavior

Implementation sequence:

```text
A2A 1.0.1 Agent Card/discovery migration
        ↓
Task projection
        ↓
Task get/cancel
        ↓
waiting/input-required + resume
        ↓
spec-native asynchronous behavior
        ↓
message/task streaming
        ↓
resubscription/interoperability gates
```

OWA must not invent proprietary async/stream extensions when the pinned protocol defines native semantics.

Streaming reuses the common bounded lifecycle/event infrastructure and must not expose engine-native checkpoint or stream objects.

## 14. A2A push notifications

Push notifications remain deferred because they create an outbound callback trust boundary requiring callback allowlisting, SSRF protection, TLS/server identity verification, callback authentication, replay/idempotency protection, bounded retries/dead-letter behavior, and secret-safe observability.

This does not block the core Task/get/cancel/stream profile.

## 15. Multi-tenancy

Multi-tenancy remains outside the current product scope.

New security/profile/persistence structures should avoid obvious design choices that would prevent future tenant isolation, but no tenant model, tenant routing, or tenant-aware authorization should be implemented now.

## 16. Sandbox contract naming

Keep the two concepts separate:

```text
sandbox/contract.py
```

owns backend request/result/interface contracts.

The portable requirements/capability SPI currently named:

```text
sandbox_contract.py
```

should be renamed to:

```text
sandbox_capabilities.py
```

or an equivalent capability-oriented name. Do not merge the two responsibilities.

## 17. Required tests before capability advertisement

For every protocol/security expansion, tests must cover as applicable:

- strict configuration parsing;
- YAML/environment override behavior;
- secret-reference resolution and non-disclosure;
- authentication success/failure;
- authorization allow/deny by principal, scope, permission/action, and resource;
- protocol-version metadata accuracy;
- malformed/oversized request rejection;
- sanitized errors;
- timeout/cancellation behavior;
- persistence/restart behavior for durable Tasks/state;
- cross-engine observable parity;
- authoritative interoperability/conformance fixtures where available.

Shared contract tests remain the portability proof.
