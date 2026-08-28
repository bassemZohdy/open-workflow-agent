# Protocol and Security Architecture Decisions

Date: 2026-08-28

This document records the project-wide decisions for external protocol baselines, authentication/authorization configuration, A2A skill/task semantics, and the sandbox contract naming cleanup.

`Project Definition.md` remains authoritative. This document refines how protocol integrations and security configuration must be implemented.

## 1. External protocol version policy

Open Workflow Agent targets the **latest stable released version** of every external protocol/specification it implements or advertises.

This rule applies to the current and planned protocol surface, including:

- Open Workflow Specification;
- A2A;
- MCP;
- OpenAPI;
- CloudEvents;
- future AsyncAPI bindings;
- future gRPC protocol bindings where a versioned application protocol is involved.

The rule is not a floating runtime dependency on whatever becomes latest. Each release must use an explicitly reviewed and pinned protocol baseline.

Required process:

```text
latest stable release from authoritative source
        ↓
compatibility/security review
        ↓
pin exact project baseline/schema/fixtures
        ↓
implementation or migration
        ↓
deterministic contract/conformance/interoperability tests
        ↓
capability advertisement
        ↓
release
```

Draft, preview, RC, editor-draft, or unreleased protocol revisions must not be the default production contract.

Older versions may remain available only when there is demonstrated interoperability value and the compatibility behavior is explicit, bounded, and tested. Compatibility aliases must not cause the runtime to claim support for a protocol version whose semantics it has not implemented.

A protocol-version change is an architecture compatibility change, not an ordinary dependency bump.

## 2. Protocol capability advertisement

`/v1/capabilities` and protocol-specific discovery metadata must advertise only the protocol/version/features that the runtime has deterministically verified.

A valid upstream protocol specification does not imply that OWA implements its complete surface.

Preferred wording is:

```text
<Protocol> <version> bounded profile
```

until the applicable conformance/interoperability suite justifies a broader claim.

For example, A2A must not be described as fully conformant while OWA implements only a bounded subset of Task, streaming, push-notification, or transport behavior.

## 3. Authentication and authorization are deployment configuration

Authentication and authorization are runtime/deployment concerns and must be externally configurable.

They must not be hard-coded into A2A, MCP, HTTP/OpenAPI, approval, administrative, or future protocol implementations.

Configuration precedence remains:

```text
built-in defaults
      ↓
YAML configuration
      ↓
environment-variable overrides
```

Environment-variable overrides use the normal `OWA__...` nested configuration convention.

Security configuration includes, where applicable:

- authentication mechanism;
- credential reference;
- client/agent identity;
- scopes;
- roles;
- skill/action authorization;
- audience/resource constraints;
- TLS/client-certificate profile;
- OAuth2/OIDC metadata references;
- token-exchange/delegation policy when supported;
- inbound and outbound security policy.

## 4. Named security profiles

Protocol integrations should reference reusable named security profiles rather than duplicating raw authentication configuration in each protocol block.

Conceptual configuration:

```yaml
security:
  profiles:
    partner-agent:
      type: bearer
      token:
        fromEnv: A2A_PARTNER_TOKEN
      authorization:
        skills:
          - residence-renewal
          - residence-status
        actions:
          - message.send
          - tasks.get

    internal-oauth:
      type: oauth2
      client_id:
        fromEnv: INTERNAL_CLIENT_ID
      client_secret:
        fromEnv: INTERNAL_CLIENT_SECRET
      token_endpoint: https://identity.example.com/oauth2/token
      scopes:
        - workflow.invoke
```

Protocol configuration then references the profile:

```yaml
a2a:
  security_profile: partner-agent

mcp:
  servers:
    customer-data:
      url: https://mcp.example.com
      security_profile: internal-oauth
```

Exact field names may change during implementation to fit the strict Pydantic configuration model, but the ownership boundary must remain the same.

## 5. Secret handling

Workflow definitions must not contain raw credentials.

Preferred deployment pattern:

```yaml
token:
  fromEnv: A2A_PARTNER_TOKEN
```

The real secret should be supplied through an appropriate deployment mechanism such as:

- Kubernetes Secret;
- OpenShift Secret;
- Docker secret/environment injection;
- Vault;
- External Secrets Operator or equivalent secret manager.

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

The project may continue to support explicit environment overrides for local development, but production documentation should prefer secret managers over committed YAML or shared `.env` files.

## 6. Enterprise identity boundary

OWA may provide bounded local authentication/authorization, such as named credentials with per-skill/action scopes.

OWA must not become an identity provider.

Enterprise OAuth2/OIDC, client authentication, user delegation, token exchange, consent, and federation should remain compatible with deployment identity infrastructure such as an API gateway, service mesh, Keycloak-compatible identity provider, or another standards-compliant authorization server.

A2A message/task data must not be used as a hidden channel for bearer tokens or delegated user credentials.

## 7. A2A skill ownership

The A2A runtime should support multiple deployment-configured skills backed by explicitly registered workflows.

Conceptually:

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

Routing rule:

```text
A2A skill id
     ↓
deployment-owned mapping
     ↓
registered workflow
```

An A2A client must never be allowed to choose an arbitrary workflow file, filesystem path, catalog entry, or execution backend.

The Agent Card advertises only configured skills that are actually enabled by the deployment and supported by the current runtime profile.

## 8. A2A Task model

A2A Tasks must be a protocol projection over common OWA invocation state, not a second workflow/execution engine.

Preferred identity relationship:

```text
A2A task_id == OWA invocation_id
```

unless a verified protocol requirement later requires a separate external identifier.

Conceptual state mapping:

```text
OWA invocation              A2A Task
-----------------------------------------
running                     working
waiting                     input-required
completed                   completed
faulted                     failed
cancelled                   canceled
```

The exact mapping must be validated against the pinned stable A2A specification before implementation.

Existing common runtime components remain authoritative for:

- `ExecutionHandle`;
- workflow fingerprint;
- persistence;
- resume;
- cancellation;
- durable approval state;
- lifecycle events;
- engine-native checkpoint references.

A2A must not introduce duplicate persistence/checkpoint semantics.

## 9. A2A synchronous, asynchronous, and streaming behavior

The existing bounded synchronous send path may remain while the Task profile is introduced.

Asynchronous behavior must use the semantics defined by the pinned stable A2A specification. OWA must not invent a proprietary `async: true` protocol extension merely for convenience.

Implementation sequence:

```text
stable Agent Card/discovery migration
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

Streaming should reuse the common bounded event/lifecycle infrastructure and map those events into A2A protocol semantics. It must not expose ADK/LangGraph native checkpoint or stream objects.

## 10. A2A push notifications

Push notifications remain deferred because they create a distinct outbound callback trust boundary.

Before implementation the project must define and test:

- callback endpoint allowlisting;
- SSRF protection;
- TLS/server identity verification;
- callback authentication;
- replay/idempotency protection;
- bounded retries;
- dead-letter/failure policy;
- secret-safe logging and observability.

This work must not block the core Task/get/cancel/stream interoperability profile.

## 11. Sandbox contract naming

The two sandbox contract concepts remain separate:

```text
sandbox/contract.py
```

continues to own the runtime backend request/result/interface contract.

The portable sandbox requirements/capability SPI currently named:

```text
sandbox_contract.py
```

should be renamed to a capability-oriented name such as:

```text
sandbox_capabilities.py
```

They should not be merged because they represent different architectural responsibilities.

## 12. Required tests before capability advertisement

For every protocol/security expansion, tests must cover at least:

- strict configuration parsing and environment override behavior;
- secret-reference resolution and secret non-disclosure;
- authentication success/failure;
- authorization scope/skill/action denial;
- protocol-version metadata accuracy;
- malformed/oversized request rejection;
- sanitized errors;
- timeout/cancellation behavior;
- persistence/restart behavior where the protocol exposes durable Tasks/state;
- cross-engine observable parity for portable behavior;
- authoritative protocol interoperability/conformance fixtures where available.

No capability becomes portable merely because multiple adapters contain similarly named code; the shared contract tests remain the portability proof.

## 13. Remaining architecture questions

The following do not block the decisions above but should be addressed in later slices:

1. **Security-profile schema details** — exact supported profile types and fields for bearer, API key, OAuth2/OIDC, mTLS, and future delegated-user flows.
2. **Protocol compatibility lifetime** — how many older stable protocol baselines, if any, OWA will support concurrently after a stable-version migration.
3. **Fine-grained authorization vocabulary** — standardize action names (`message.send`, `tasks.get`, etc.) and whether roles map to scopes internally or remain separate concepts.
4. **Rate/concurrency policy** — decide whether common security profiles also carry inbound rate/concurrency limits or whether those remain a separate traffic-policy configuration.
5. **Tenant boundary** — if OWA later becomes multi-tenant, security profiles, skills, persistence namespaces, and protocol identities must be tenant-isolated. Multi-tenancy is currently outside the product scope.
6. **Delegated user identity** — define only when there is a concrete enterprise A2A/MCP use case; it should build on standards-based identity/token exchange rather than custom message fields.

These questions should remain explicit rather than being accidentally encoded through one protocol implementation.
