# Open Workflow Agent — Project Definition

Working repository:

```text
bassemZohdy/open-workflow-agent
```

Primary production runtime images:

```text
open-workflow-agent-adk
open-workflow-agent-langgraph
```

The project name and public application contract are intentionally independent of ADK, LangGraph, or any future execution framework.

---

# 1. Definition

Open Workflow Agent (OWA) is a lightweight, configuration-driven, model-agnostic agent and workflow runtime that executes Open Workflow definitions through interchangeable agent-runtime engines.

The external application definition is composed from:

```text
Open Workflow Specification
+ agent configuration
+ model configuration
+ knowledge
+ memory
+ tools
+ deployment/runtime policy
```

Initial production engines:

```text
ADK
LangGraph
```

Optional evaluation engine:

```text
Microsoft Agent Framework
```

The same external configuration and workflow should produce equivalent observable behavior on every engine that advertises the required portable capabilities.

Frameworks are implementation technologies. They are not the public application contract.

---

# 2. Product Goal

A useful runtime should start with minimal configuration:

```yaml
model:
  name: provider/model-name
```

The same packaged runtime becomes more capable by adding configuration or mounted resources rather than application-specific orchestration code.

```text
model only
  -> simple agent

model + knowledge
  -> RAG agent

model + persistence/memory
  -> durable conversational agent

model + tools
  -> tool-using agent

model + workflow
  -> deterministic/agentic workflow

workflow + agent + llm + HTTP/MCP/A2A/OpenAPI
  -> complex portable agentic orchestration
```

No application code should need to change between these scenarios.

---

# 3. Fundamental Runtime Rule

## Every invocation is a workflow invocation.

There is no separate “simple agent” execution path.

Bad:

```python
if workflow_exists:
    execute_workflow()
else:
    execute_agent()
```

Required:

```python
workflow = configured_workflow or generate_default_workflow()
execute(workflow)
```

Therefore:

> Workflow is mandatory internally and optional externally.

If a workflow is not supplied, OWA generates the default one-task Open Workflow definition that calls:

```text
agent:1.0.0@default
```

---

# 4. Stable Public Contracts

The following are public engine-neutral contracts:

```text
agent/model/runtime configuration
Open Workflow definition
runtime catalog functions
HTTP API
invocation identity
resume/cancellation semantics
portable workflow behavior
capability advertisement
common runtime errors
common lifecycle events
protocol boundaries exposed by OWA
```

Public clients must never need:

```text
ADK run/node identifiers
LangGraph thread/checkpoint identifiers
framework checkpoint payloads
engine-native stream objects
```

---

# 5. Open Workflow Baseline

OWA currently targets:

```text
Open Workflow Specification 1.0.3
```

The official schema is used unchanged.

Do not fork the Open Workflow schema and do not create proprietary authoring syntax for AI operations.

AI-native operations are represented through standard catalog/custom-function mechanisms such as:

```text
agent:1.0.0@default
llm:1.0.0@default
```

OWA must not claim full Open Workflow conformance until the applicable CTK/compatibility gates prove it.

Use wording such as:

```text
Open Workflow 1.0.3 based runtime
OWA Portable Profile v1
```

when broader conformance has not been established.

---

# 6. Canonical Execution Plan

The runtime uses one internal, immutable, typed execution representation so multiple engines do not independently reinterpret Open Workflow semantics.

```text
Open Workflow
    |
    v
validate + normalize
    |
    v
Canonical Execution Plan
    |
    +----> ADK compiler/runtime
    |
    +----> LangGraph compiler/runtime
```

The Execution Plan is:

```text
internal
immutable
typed
derived
not a user-authored format
not a second workflow DSL
```

Open Workflow remains the only workflow authoring language.

The plan preserves canonical Open Workflow task references and source locations for diagnostics, lifecycle events, and contract tests.

---

# 7. Compilation Pipeline

```text
WorkflowSource
  -> WorkflowLoader
  -> official SchemaValidator
  -> CapabilityValidator
  -> SemanticNormalizer
  -> PlanBuilder
  -> immutable ExecutionPlan
  -> EngineCompiler
  -> Engine execution
```

A valid Open Workflow document is not automatically supported by the current OWA Portable Profile.

Unsupported-but-valid Open Workflow behavior must fail explicitly with an engine-neutral unsupported-feature error. It must never be silently ignored.

---

# 8. Portable Profile and Engine Capabilities

Portable behavior is proven through shared contract tests, not documentation claims.

The common profile includes the Open Workflow/data semantics implemented equivalently by production engines. Optional engine capabilities may differ and must be advertised explicitly through:

```text
GET /v1/capabilities
```

Do not reduce all engines to the lowest common denominator. Engine-specific capabilities are allowed as long as workflows that depend on them are correctly identified as less portable.

A capability is considered portable only when the same fixture and inputs produce the expected equivalent observable result across the applicable production engines.

---

# 9. Engine SPI

The common engine interface must expose only portable concepts.

Conceptually:

```python
class WorkflowEngine:
    async def initialize(self, services): ...
    async def compile(self, plan): ...
    async def invoke(self, workflow, invocation): ...
    async def resume(self, handle, resume_input): ...
    async def cancel(self, handle): ...
    def capabilities(self): ...
    async def shutdown(self): ...
```

Core must never import ADK or LangGraph.

Engine packages depend on core, not the reverse.

Engine-owned responsibilities include:

```text
framework-native workflow construction
node/task lifecycle integration
native checkpoint/session integration
resume/cancellation integration
framework callbacks
framework-native agent construction
tool wrapping
```

Core-owned responsibilities include:

```text
configuration
Open Workflow loading/validation/normalization
jq/data semantics
portable execution plan
runtime catalog
knowledge
memory interface
common protocols
public API
common lifecycle/error contract
common capability model
```

---

# 10. Engine-Native Durability

Do not build a second checkpoint engine in core.

```text
ADK
  -> ADK-native durable/session mechanisms

LangGraph
  -> LangGraph-native checkpointer/store mechanisms
```

The common runtime owns public invocation identity, workflow fingerprint, status, and portable metadata.

Engine-specific checkpoint state remains private to the engine adapter.

---

# 11. Invocation Identity

Public identity:

```text
invocation_id
session_id
user_id
```

`user_id` is application/correlation information unless authenticated identity is established by deployment security. It must not be treated automatically as a security principal.

Internally, OWA maintains an engine-neutral execution handle:

```text
ExecutionHandle
  invocation_id
  engine
  engine_execution_reference
  user_id
  session_id
  workflow_name
  workflow_version
  workflow_fingerprint
  status
```

Public resume/cancel APIs use `invocation_id`, never engine-specific identifiers.

---

# 12. Workflow Mutation and Resume

Persist a fingerprint of the normalized workflow definition with durable invocation metadata.

Resume must verify:

```text
stored workflow fingerprint == currently loaded workflow fingerprint
```

If the definition changed unexpectedly, fail with an engine-neutral workflow-definition-changed error unless a future explicit migration mechanism exists.

---

# 13. Models and Agents

Model configuration is common; framework-specific model objects are not.

The first generic provider abstraction is LiteLLM so one packaged runtime can support multiple providers through external configuration.

Keep direct LLM calls separate from agent calls:

```text
llm:1.0.0@default
  -> direct model invocation

agent:1.0.0@default
  -> configured autonomous/tool-using agent
```

This allows workflows to combine deterministic orchestration, cheap model reasoning, external calls, and autonomous agents without forcing every AI operation through an agent loop.

---

# 14. Knowledge, Memory, Session, and Checkpoints

These are separate concepts with separate lifecycles.

```text
Knowledge
  externally supplied information

Session
  current interaction/execution state

Memory
  information carried across interactions

Engine checkpoint state
  durable execution-resume state owned by the selected engine
```

Never collapse these into a single generic RAG/state abstraction.

Knowledge remains common core functionality. Engine adapters expose the same knowledge retriever as native tools.

Mounted `/knowledge` content should work without requiring another paid embedding API; the standard images package a local embedding path.

---

# 15. Persistence

A configured datasource may provide storage for:

```text
common invocation metadata
memory
knowledge metadata
approvals
schedules
sandbox metadata
engine-native persistence
```

Each subsystem uses isolated tables/namespaces and lifecycle ownership.

SQLite is the reference datasource. PostgreSQL support is provided through locked optional dependencies with isolated engine/common namespaces.

One engine must never read another engine's checkpoint format.

---

# 16. Common Protocol Services

Where practical, protocol behavior belongs in core:

```text
HTTP
MCP
A2A
OpenAPI
```

The same common protocol client/service can then be exposed to:

```text
Open Workflow calls
ADK agent tools
LangGraph agent tools
```

This avoids implementing a protocol separately in every engine adapter.

Protocol baseline and capability advertisement remain explicit. A pinned baseline is a compatibility target, not by itself a conformance claim.

Current reviewed stable baselines are maintained in `docs/protocol-baselines.md`.

---

# 17. A2A Architecture and Current Boundary

OWA targets stable A2A release:

```text
1.0.1
```

and advertises protocol version:

```text
1.0
```

only for the implemented bounded profile.

Inbound A2A is optional, deployment-controlled, and disabled by default.

Current bounded server boundary:

```text
GET  /.well-known/agent-card.json
POST <configured A2A path>                 JSON-RPC SendMessage
POST <configured A2A path>/message:send    HTTP+JSON
```

Supported transports:

```text
jsonrpc    default
http_json
```

The Agent Card uses stable-v1 `supportedInterfaces` metadata and a deployment-configured public base URL.

Legacy A2A v0.3 discovery paths, method aliases, and Part shapes are intentionally not preserved.

Current bounded behavior includes:

```text
Agent Card discovery
synchronous SendMessage
v1 message/Part shapes
request/message size bounds
sanitized transport errors
optional temporary bearer guard
exact features.a2a capability advertisement
```

Not yet part of the implemented bounded profile:

```text
persistent A2A Tasks
Task get/cancel
input-required/resume mapping
protocol-native async Task-returning behavior
A2A streaming/resubscription
push notifications
broad/full conformance claim
```

---

# 18. A2A Tasks Are Projections, Not Another Engine

A2A Task state must project common OWA invocation state.

Conceptually:

```text
A2A Task
  task_id
  context_id
  status
  messages
  artifacts
      |
      v
OWA invocation_id / ExecutionHandle
      |
      v
selected engine native state
```

Prefer:

```text
A2A task_id == OWA invocation_id
```

unless the pinned A2A specification requires a distinct external identity.

Common invocation, persistence, resume, cancellation, approval, schedule, and lifecycle services remain authoritative.

Do not create a separate A2A workflow runtime or checkpoint store.

Recommended expansion order:

```text
shared security profiles
  -> deployment-declared A2A skills
  -> Task projection
  -> Task retrieval/cancellation
  -> waiting/input-required/resume mapping
  -> protocol-native async behavior
  -> A2A streaming/resubscription
  -> interoperability/conformance gates
```

---

# 19. A2A Skills

A2A skills are deployment-owned and map only to explicitly registered workflows.

Conceptual configuration:

```yaml
a2a:
  skills:
    - id: residence-renewal
      name: Residence Renewal
      workflow: residence-renewal
```

An A2A client must never choose an arbitrary workflow path, file, catalog entry, sandbox backend, or engine-native execution target.

---

# 20. Streaming

General portable token/output streaming is not required by the Portable Profile.

OWA does provide a bounded common lifecycle SSE observation boundary:

```text
GET /v1/events/lifecycle/stream
```

That stream is engine-neutral and includes bounded replay/backpressure/lifetime controls. It is not itself an A2A protocol binding and it does not expose engine-native checkpoints or stream objects.

A2A streaming must be added only after the A2A Task/message/artifact lifecycle contract is stable. It may reuse common stream mechanics but must emit protocol-native A2A updates rather than raw runtime lifecycle events.

Disconnecting an observation stream must not implicitly cancel an invocation unless the relevant protocol contract explicitly requires cancellation.

---

# 21. Push Notifications

A2A push notifications remain deferred because they create an outbound callback trust boundary.

Do not implement them until deployment policy covers:

```text
callback allowlisting
TLS/server identity verification
callback authentication
SSRF protection
replay/idempotency protection
bounded retry/dead-letter behavior
secret-safe logging/observability
```

Push notification support is not required to complete the next bounded Task/streaming profile.

---

# 22. Security Model

Workflow files are trusted deployment artifacts. Invocation/event/tool input is untrusted data.

Authentication and authorization are runtime/deployment configuration and must not be encoded as raw credentials inside workflows.

The target shared named security profile mechanisms are intentionally limited initially to:

```text
bearer
api_key
oauth2_client_credentials
mtls
```

Protocol/tool configuration references named profiles. Sensitive values are resolved from deployment secret/environment mechanisms.

Secrets must never appear in:

```text
workflow definitions
execution plans
capability responses
Agent Cards
lifecycle events
A2A Tasks/artifacts
logs
sandbox output
persisted invocation metadata
```

Current protocol-specific bearer fields are temporary pre-stable implementation details, not long-term compatibility contracts.

---

# 23. Authorization Vocabulary

Use these terms consistently:

```text
principal / identity
role
scope
permission / action
resource
audience
```

Roles and scopes are not synonyms.

Use protocol-native actions where practical, for example:

```text
message.send
tasks.get
tasks.cancel
```

Authorization should support least-privilege decisions per principal/action/resource.

---

# 24. Enterprise Identity Boundary

OWA must not become an identity provider.

These remain deployment/identity-platform responsibilities:

```text
OAuth2/OIDC federation
token exchange
delegated user identity
consent
cross-domain identity trust
```

Delegated-user identity does not block the basic A2A Task projection and should be introduced only when a concrete enterprise requirement exists.

---

# 25. Traffic Policy Is Separate from Security Policy

Authentication/authorization answers:

```text
who is the caller?
what may the caller do?
```

Traffic policy answers:

```text
how much traffic may be admitted?
```

Rate limits, concurrency, burst/admission control, and future circuit policies belong to a separate deployment-controlled traffic policy model. Do not fold them into security profiles.

---

# 26. Side Effects, Retry, and Idempotency

Any side-effecting operation may be retried or re-executed around failures/resume.

Examples:

```text
payment
email
database mutation
external API command
A2A action
MCP write tool
sandbox execution
```

Whenever practical support:

```text
idempotency keys
deduplication
operation identifiers
```

Do not assume a checkpoint implies exactly-once side effects.

---

# 27. Sandbox Execution

Executable workflow operations are disabled by default and cannot be enabled by workflow content alone.

All engines route execution through one common `SandboxManager` policy/contract.

Backends:

```text
internal process sandbox
restricted Docker controller
restricted Kubernetes/OpenShift controller
```

The internal process sandbox is a controlled execution boundary, not a hard isolation boundary.

External backends provide stronger isolation and deployment-owned image/resource/network/secret policy.

The main runtime must not receive an unrestricted Docker socket or cluster-wide Kubernetes permissions.

Kubernetes real-cluster acceptance is green. OpenShift-specific SCC/security-context/arbitrary-UID acceptance remains deferred until an OpenShift cluster is available.

---

# 28. External Catalogs and HITL

External catalogs are disabled by default and require deployment-controlled trust policy, including endpoint/host constraints, TLS, bounded responses, digest/version checks where required, and safe caching/revalidation.

Durable HITL approvals are implemented as a common persisted layer composed with standard event/listen semantics rather than a proprietary workflow task.

Generic eventing remains conceptually separate from durable approval state.

---

# 29. Lifecycle Events and Observability

Create and preserve a common engine-neutral lifecycle model such as:

```text
WorkflowStarted
WorkflowWaiting
WorkflowResumed
WorkflowCompleted
WorkflowFaulted
WorkflowCancelled

TaskStarted
TaskWaiting
TaskCompleted
TaskFaulted
TaskRetried
TaskCancelled
```

Events and logs should correlate through stable common identifiers such as:

```text
invocation_id
session_id
workflow name/version
Open Workflow task reference
engine
duration
status
```

Framework-native identifiers may be retained internally for diagnostics but must not become the public application contract.

---

# 30. Error Contract

Framework exceptions are translated at the engine/protocol boundary into sanitized engine-neutral runtime errors.

Representative categories:

```text
ConfigurationError
WorkflowSchemaError
WorkflowSemanticError
UnsupportedWorkflowFeature
ExpressionError
ModelError
AgentError
KnowledgeError
MemoryError
ToolError
WorkflowExecutionError
WorkflowDefinitionChanged
```

Preserve original framework errors internally for diagnostics without leaking secrets or framework-native state publicly.

---

# 31. Docker and Deployment Principles

Production engines are packaged independently.

Do not build one image containing every engine framework and select the engine at runtime.

Benefits:

```text
smaller dependency graph
smaller attack surface
fewer dependency conflicts
clear runtime identity
independent engine upgrades
```

Runtime images expose the same public configuration and standard paths:

```text
/config
/knowledge
/data
port 8080
```

Containers must run non-root, avoid requiring a fixed UID, support read-only-root deployments where practical, expose readiness/liveness, handle SIGTERM gracefully, and externalize state.

Do not install dependencies dynamically at container startup.

---

# 32. Dependency Isolation

ADK and LangGraph dependencies remain independently lockable.

```text
engines/adk/uv.lock
engines/langgraph/uv.lock
```

Core remains framework-neutral and should keep only dependencies required by shared contracts/services.

An engine upgrade must not unnecessarily block another engine's image.

---

# 33. Testing Strategy

No deterministic CI test requires a paid model API.

Use a shared deterministic `FakeModel` capable of representative response/tool/failure behavior.

Testing layers:

```text
core tests
engine-native tests
shared contract tests
Open Workflow CTK subsets
protocol compatibility/interoperability tests
persistence/restart tests
container E2E tests
sandbox acceptance
release/supply-chain gates
```

Shared contract tests are the proof of portability.

A feature must not be marked shipped merely because two adapters contain code for it.

---

# 34. Protocol Version Policy

OWA targets the latest stable released version of protocols/specifications it implements or advertises, but version changes are explicit compatibility/security reviews.

```text
upstream stable release
  -> review
  -> pin baseline
  -> migrate implementation
  -> deterministic tests
  -> capability metadata update
  -> OWA release
```

Draft/preview/RC/editor-draft revisions do not become production defaults.

Before the OWA public contract stabilizes, legacy protocol generations may be removed rather than automatically maintained as compatibility layers.

---

# 35. Current Protocol Baselines

The detailed verified record lives in `docs/protocol-baselines.md`.

Current reviewed baselines:

```text
Open Workflow Specification  1.0.3
A2A Protocol                 1.0.1
Model Context Protocol       2026-07-28
OpenAPI Specification        3.2.0
CloudEvents                  1.0.2
AsyncAPI Specification       3.1.0
```

`/v1/capabilities`, Agent Cards, protocol metadata, and release documentation may advertise only implemented and tested behavior.

---

# 36. Current Release and Project State

Current formal release:

```text
v0.1.0
```

Current `main` includes unreleased post-v0.1.0 protocol/A2A work.

Verified major delivered areas include:

```text
ADK + LangGraph runtime parity for the current Portable Profile
SQLite/PostgreSQL persistence boundaries
knowledge + local embeddings
memory
resume/cancellation
bounded lifecycle events/SSE
scheduling
local sub-workflows
durable HITL approvals
secure external catalogs
internal sandbox
restricted Docker sandbox backend
restricted Kubernetes sandbox backend + real-cluster acceptance
bounded inbound A2A stable-v1 profile
optional Microsoft Agent Framework adapter (not a production target)
```

The active ordered backlog is maintained only in `TODO.md`.

---

# 37. Current A2A Completion Path

The current A2A transport/server blocker has been removed. The remaining architecture work is the protocol-level lifecycle and security projection.

Current completion path:

```text
shared named security profiles
  -> A2A skill authorization/routing
  -> persistent A2A Task projection
  -> tasks get/cancel
  -> waiting/input-required/resume mapping
  -> protocol-native async behavior
  -> streaming/resubscription
  -> interoperability/conformance gates
```

Push notifications remain separate/deferred.

This sequence keeps A2A in core and reuses common invocation/durability semantics rather than implementing engine-specific A2A code.

---

# 38. Non-Goals

Do not turn OWA into:

```text
a custom workflow language
a BPMN engine
a custom LLM framework
a custom MCP/A2A protocol
a general identity provider
a multi-tenant management platform
a visual workflow designer
a distributed scheduler by default
an unrestricted remote-code execution platform
```

New capability should be added only when it preserves the engine-neutral public contract and has a demonstrated requirement.

---

# 39. Mandatory Architectural Rules for Development Agents

1. Every invocation executes through a workflow.
2. Open Workflow is the external authoring language.
3. Do not fork the Open Workflow schema.
4. Do not expose the internal Execution Plan publicly.
5. Core must not import engine frameworks.
6. Framework-specific behavior belongs in the engine package.
7. Use framework-native checkpoint/resume mechanisms.
8. Keep Open Workflow data/expression semantics in core.
9. `jq` remains the portable default expression language.
10. Keep `agent` and `llm` catalog functions semantically separate.
11. Keep Knowledge, Memory, Session, approvals, schedules, sandbox state, common invocation metadata, and engine checkpoints separate.
12. Do not require paid APIs in deterministic tests.
13. Add/update tests before marking work complete.
14. Run shared contract tests after engine changes.
15. Do not silently ignore unsupported workflow/protocol behavior.
16. Do not add dependencies without demonstrated need.
17. Do not install dependencies at container startup.
18. Keep engine dependency locks independent.
19. Update capability metadata whenever public support changes.
20. Never log or serialize credentials/secrets.
21. Preserve Open Workflow task references through runtime layers.
22. Verify framework/protocol API usage against the pinned version.
23. Protocol changes are compatibility/security changes, not ordinary dependency bumps.
24. A2A Tasks project OWA invocation state; they do not create another execution engine.
25. Workflow definitions never choose arbitrary deployment workflows, credentials, sandbox backends, or engine-native state.
26. Authentication/authorization and traffic policy remain separate deployment concerns.
27. OWA does not own enterprise federation, user delegation, token exchange, or consent.
28. Advertise only implemented and verified protocol/capability behavior.

---

# 40. Core Architectural Decisions

## ADR-001 — Every invocation is a workflow

No separate agent execution path.

## ADR-002 — Open Workflow is the external workflow language

No proprietary workflow DSL.

## ADR-003 — Missing workflow generates the default one-task workflow

The generated workflow calls `agent:1.0.0@default`.

## ADR-004 — AI-native operations use runtime catalog functions

No schema fork for agent/LLM operations.

## ADR-005 — OWA is framework-neutral

ADK, LangGraph, and future frameworks are execution engines.

## ADR-006 — One repository, separate engine packages/images

Avoid duplicated projects and dependency pollution.

## ADR-007 — Use a canonical internal Execution Plan

One normalized semantic representation feeds all engines.

## ADR-008 — Execution Plan is not an external DSL

Open Workflow remains authoritative.

## ADR-009 — Open Workflow semantics live in core

Engines execute plans rather than redefine workflow meaning.

## ADR-010 — Durability remains engine-native

Core owns portable invocation metadata, not framework checkpoint internals.

## ADR-011 — Portability is proven with shared contract tests

Not assumed from adapter implementations.

## ADR-012 — Capabilities are explicit

Portable and engine-specific behavior are advertised precisely.

## ADR-013 — Knowledge is common core functionality

Only tool wrapping is engine-specific.

## ADR-014 — Runtime state categories remain separate

Knowledge, memory, session, invocation, approval, schedule, sandbox, and checkpoint state have distinct lifecycles.

## ADR-015 — Protocol behavior belongs in core where practical

HTTP/MCP/A2A/OpenAPI are not reimplemented per engine.

## ADR-016 — A2A Tasks are projections over common invocation state

No second A2A workflow/durability engine.

## ADR-017 — Security is deployment/runtime configuration

Workflows do not contain raw credentials.

## ADR-018 — Enterprise identity remains external

OWA is not an identity provider.

## ADR-019 — Traffic policy is separate from authentication/authorization

Identity permissions and admission limits remain distinct models.

## ADR-020 — Protocol advertisement is evidence-based

Pinned versions are targets; only implemented/tested behavior is advertised.

---

# 41. Final Architecture

```text
                         OPEN WORKFLOW AGENT
                                  |
                         Stable Public Contracts
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
        Config               Open Workflow              API
                                  |
                         Validate + Normalize
                                  |
                                  v
                       Canonical Execution Plan
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
               ADK ENGINE                LANGGRAPH ENGINE
                    |                           |
                    +-------------+-------------+
                                  |
                           Common Services
                                  |
      +-----------+-----------+-----------+-----------+-----------+
      |           |           |           |           |           |
    Models     Knowledge     Memory      Tools      Protocols   Runtime State
      |           |           |           |           |           |
   LiteLLM     Embeddings    Store      MCP/etc. HTTP/MCP/A2A  invocation/
                                                               approval/
                                                               schedule/
                                                               sandbox
```

The central value of the project is that:

```text
workflow.yaml
agent.yaml
/knowledge
```

remain stable when deployment changes from one supported engine image to another, provided the workflow uses capabilities in the common portable profile.

That portability, backed by explicit capabilities and shared contract tests, is the defining product contract of Open Workflow Agent.
