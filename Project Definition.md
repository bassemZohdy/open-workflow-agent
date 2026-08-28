# Open Workflow Agent

## Project Definition

Working repository name:

```text
open-workflow-agent
```

Primary Docker images:

```text
<org>/open-workflow-agent-adk
<org>/open-workflow-agent-langgraph
```

The project name is intentionally independent of ADK and LangGraph.

---

# 1. Project Definition

Open Workflow Agent is a lightweight, configuration-driven platform for running AI agents and deterministic/agentic workflows from the same declarative definition.

The platform uses:

```text
Open Workflow Specification
          +
Agent configuration
          +
Model configuration
          +
Knowledge
          +
Memory
          +
Tools
```

and executes them using interchangeable runtime engines.

Initial engines:

```text
ADK
LangGraph
```

The same external configuration and workflow should execute with equivalent observable behavior on either engine, subject to explicitly advertised engine capabilities.

The central abstraction is therefore:

```text
                 Open Workflow Agent
                         │
                 Stable Contracts
                         │
          ┌──────────────┴──────────────┐
          │                             │
      ADK Engine                  LangGraph Engine
```

ADK and LangGraph are implementation technologies.

They are not part of the public application contract.

---

# 2. Product Goal

A user should be able to run a useful agent using only:

```yaml
model:
  name: provider/model-name
```

The runtime provides defaults for everything else.

The same image can become more capable by mounting or configuring additional features.

For example:

```text
model only
    ↓
simple agent

model + knowledge folder
    ↓
RAG agent

model + persistent datasource
    ↓
persistent agent with memory

model + tools
    ↓
tool-using agent

model + workflow
    ↓
deterministic/agentic workflow

workflow + agent + llm + HTTP + MCP + A2A
    ↓
complex agentic orchestration
```

No application code should need to change between these scenarios.

---

# 3. Fundamental Principle

## Every invocation is a workflow invocation.

There must never be:

```python
if workflow_exists:
    execute_workflow()
else:
    execute_agent()
```

Instead:

```python
workflow = configured_workflow or generate_default_workflow()

execute(workflow)
```

Therefore:

> Workflow is mandatory internally and optional externally.

This is one of the most important architectural rules in the project.

---

# 4. Default Workflow

If no workflow is supplied, the runtime generates an implicit Open Workflow definition.

Conceptually:

```yaml
document:
  dsl: '1.0.3'
  namespace: open-workflow-agent
  name: default-agent
  version: '1.0.0'

do:
  - respond:
      call: agent:1.0.0@default
```

Therefore even the simplest chatbot executes as:

```text
Request
   ↓
Workflow invocation
   ↓
Default Open Workflow
   ↓
agent:1.0.0@default
   ↓
Configured Agent
   ↓
Response
```

There is no special "simple-agent runtime path."

---

# 5. Stable External Contracts

The following are public contracts and must remain independent from a specific execution engine.

## 5.1 Agent configuration

Defines:

```text
agent
models
knowledge
memory
tools
persistence
workflow location
server options
```

---

## 5.2 Workflow definition

Open Workflow Specification 1.x.

Initial implementation targets:

```text
1.0.3
```

---

## 5.3 Runtime catalog

Initially:

```text
agent:1.0.0@default
llm:1.0.0@default
```

---

## 5.4 HTTP API

Examples:

```text
POST /v1/invoke
POST /v1/invocations/{id}/resume
POST /v1/invocations/{id}/cancel
GET  /v1/capabilities
GET  /health/live
GET  /health/ready
POST /v1/admin/knowledge/reload
```

---

## 5.5 Runtime semantics

The following should behave consistently across engines:

```text
workflow input
task input
task output
context
expressions
branching
loops
parallelism
catalog calls
errors
workflow result
```

---

# 6. Runtime Implementations

The project initially contains two implementations.

```text
Open Workflow Agent
│
├── Core
│
├── ADK Engine
│
└── LangGraph Engine
```

These are built from one source repository but packaged into separate Docker images.

---

# 7. Why Separate Docker Images

Do not create one image containing:

```text
ADK
LangGraph
LangChain
all engine dependencies
```

and select the engine using configuration.

Instead create:

```text
open-workflow-agent-adk

open-workflow-agent-langgraph
```

Reasons:

```text
smaller containers
smaller dependency graph
less attack surface
fewer dependency conflicts
clear troubleshooting
clear runtime identity
independent framework upgrades
```

The application configuration remains identical.

The selected Docker image determines the execution engine.

Therefore configuration does not require:

```yaml
engine: adk
```

---

# 8. Overall Architecture

```text
                       HTTP API
                          │
                          ▼
                  Invocation Service
                          │
                          ▼
                 Configuration Model
                          │
                          ▼
               Workflow Resolution
                   /             \
             supplied          generated
             workflow          default
                   \             /
                    ▼           ▼
                Open Workflow 1.0.3
                         │
                         ▼
                 Schema Validation
                         │
                         ▼
                Semantic Validation
                         │
                         ▼
                    Normalizer
                         │
                         ▼
               Canonical Execution Plan
                         │
               ┌─────────┴─────────┐
               │                   │
               ▼                   ▼
         ADK Compiler       LangGraph Compiler
               │                   │
               ▼                   ▼
        ADK Workflow         LangGraph Workflow
               │                   │
               └─────────┬─────────┘
                         ▼
                  Runtime Services
                         │
          ┌──────────────┼──────────────┐
          │              │              │
        Models        Knowledge       Memory
          │              │              │
        Tools         Vector Store    Persistence
          │
   HTTP/MCP/A2A/etc.
```

---

# 9. Revised Decision: Internal Canonical Execution Plan

For the original ADK-only design, introducing another workflow representation was unnecessary.

That changes when multiple engines are supported.

Without a neutral internal representation we would have:

```text
Open Workflow
    │
    ├── parse again → ADK
    │
    └── parse again → LangGraph
```

which would eventually cause semantic differences.

Instead:

```text
Open Workflow
       │
       ▼
Canonical Execution Plan
       │
       ├── ADK
       └── LangGraph
```

---

# 10. The Execution Plan Is NOT Another Workflow Language

This distinction is critical.

Do not expose:

```text
our-workflow.yaml
execution-plan.yaml
internal-workflow.json
```

to users.

The plan is:

```text
internal
immutable
typed
derived
not persisted as an authoring format
```

Open Workflow remains the only workflow DSL.

The plan only normalizes Open Workflow into structures that are easier for runtime engines to consume.

---

# 11. What the Execution Plan Contains

Conceptually:

```text
WorkflowPlan
├── metadata
├── input transformation
├── output transformation
├── context definition
└── tasks
```

Each task becomes something like:

```text
TaskPlan
├── id/reference
├── source JSON pointer
├── type
├── input transformation
├── condition
├── output transformation
├── export transformation
├── timeout
├── error behavior
└── task-specific definition
```

Specific plan types may include:

```text
SequencePlan
CallPlan
SetPlan
SwitchPlan
ForPlan
ForkPlan
TryPlan
WaitPlan
```

Use typed Python models.

Prefer:

```text
frozen dataclasses
```

or equivalent immutable models.

---

# 12. The Plan Must Not Redefine Open Workflow Semantics

For example:

```yaml
input:
  from: ${ .customer }
```

must not become some proprietary transformation semantics.

The plan merely stores:

```text
expression = ".customer"
expressionLanguage = jq
```

The common runtime expression service evaluates it according to Open Workflow rules.

Similarly:

```yaml
switch:
```

remains an Open Workflow switch semantically.

---

# 13. Compilation Pipeline

Implement the workflow pipeline as distinct stages.

```text
WorkflowSource
     ↓
WorkflowLoader
     ↓
SchemaValidator
     ↓
CapabilityValidator
     ↓
SemanticNormalizer
     ↓
PlanBuilder
     ↓
ExecutionPlan
     ↓
EngineCompiler
```

Each component has one responsibility.

---

# 14. Schema Validation

Validate workflow definitions against the official Open Workflow 1.0.3 JSON Schema.

Do not create a modified schema.

Do not add proprietary:

```yaml
call: llm
```

schema definitions.

Runtime AI operations use standard Open Workflow custom functions.

---

# 15. Runtime Capability Validation

A valid Open Workflow document is not necessarily supported by the current runtime version.

For example:

```yaml
run:
  shell:
```

may be valid Open Workflow but intentionally disabled by Open Workflow Agent.

Therefore:

```text
SchemaValidator
        ↓
CapabilityValidator
```

must exist separately.

Unsupported functionality must produce:

```text
UnsupportedWorkflowFeature
```

rather than being silently ignored.

---

# 16. Open Workflow Runtime Profile

Initial versions must NOT claim full Open Workflow conformance.

Define:

```text
OWA Portable Profile v1
```

Initial required tasks:

```text
do
call
set
switch
for
fork
```

Initial required standard calls:

```text
http
```

Initial custom calls:

```text
agent:1.0.0@default
llm:1.0.0@default
```

Both engines must implement the complete Portable Profile.

---

# 17. Future Portable Profile

Incrementally add:

```text
try
retry
timeout
wait
raise
run workflow
MCP
A2A
OpenAPI
gRPC
AsyncAPI
```

Only after both engines pass the relevant contract tests should a capability become part of the portable profile.

The bounded eventing slice is now supported by both engines:

```text
emit: publish one event envelope to the process-local event bus
listen: await one matching event and read data, envelope, or raw JSON
```

The bus is non-durable and has no replay, broker, `all`/`any` strategy, or
subscription iterator semantics. Those features remain unsupported until a
later milestone proves their contracts.

The optional lifecycle CloudEvents boundary is supported by both engines:

```text
GET /v1/events/lifecycle?limit=100
Content-Type: application/cloudevents-batch+json
```

It returns a bounded in-memory snapshot of CloudEvents 1.0 structured JSON.
The boundary is non-streaming and non-durable; it exposes only common lifecycle
identifiers and sanitized error codes, never engine-native checkpoint state.

The bounded scheduling profile is also supported by both engines:

```text
POST /v1/schedules
GET  /v1/schedules/{schedule_id}
POST /v1/schedules/{schedule_id}/cancel
```

The configured workflow may declare exactly one of `schedule.after` or
`schedule.every`. `after` creates one delayed start; `every` creates recurring
starts after successful completion. Schedule metadata is durable in the common
runtime store, dispatch is owned by one runtime process, and leases allow a
restart to reclaim an interrupted dispatch. Execution remains at-least-once.
`cron`, event-triggered `on`, distributed scheduler ownership, and streaming are
not part of the bounded profile.

The bounded sub-workflow profile supports the standard `run.workflow` task for
workflow definitions explicitly registered in local configuration:

```yaml
workflow:
  catalog:
    - document: { dsl: "1.0.3", namespace: "example", name: "child", version: "1.0.0" }
      do:
        - finish:
            set:
              done: true
```

The child gets a new common invocation and session identity and uses the
selected engine's native execution/checkpoint path. Common lifecycle events
retain `parent_invocation_id` and `parent_task_reference`. Shell/script `run`
variants and remote or external catalog resolution remain unsupported.

### Bounded durable HITL and external-catalog profile

Open Workflow 1.0.3 has no separate `approval` task, so durable HITL composes
the existing event contract rather than introducing a proprietary task. This
slice is now implemented and portable across both engines, behind explicit
deployment configuration.

Two distinct mechanisms must not be conflated:

- Generic `emit`/`listen` eventing remains process-local and non-durable. It
  has no approval inbox, replay, durable delivery, or operator identity, and a
  pending generic event is not guaranteed to survive process restart.
- Durable approval state is a separate persisted layer: approval request and
  decision records live in the common runtime store, and a terminal decision
  replays through the normal `listen` path after restart.

Implemented approval behavior:

```text
GET  /v1/approvals                      operator-protected inbox listing
GET  /v1/approvals/{approval_id}        operator-protected record read
POST /v1/approvals/{approval_id}/decision  bearer-authorized, idempotent decision
```

- A workflow requests approval with a standard `emit` task and waits with the
  standard `listen` task using a deterministic `one.with` filter. Event `data`
  is untrusted workflow input and must be validated by the workflow's
  input/output schemas before it affects a side effect.
- Operator decisions require bearer authorization (deployment-provided
  `approvals.operator_token`) plus an explicit operator identity header, and
  are idempotent: repeated decisions on a terminal approval are rejected.
- Approval request/decision state survives process restart; terminal decisions
  replay through `listen` so a resumed invocation observes the decision.
- `approvals.enabled` gates the feature (disabled by default). When enabled,
  `/v1/capabilities` reports `features.approvals` with `approval`, `durable`,
  `replay`, and `operatorAuthorization: "bearer"`.
- The bearer/operator-header guard is deliberately a bounded deployment
  authorization boundary, not a replacement for an enterprise identity
  provider. Expiry and idempotency apply to approval records; deployment
  authentication and authorization remain outside the portable core.

Implemented external-catalog profile (fail-closed, disabled unless a
deployment configures catalog trust):

- An Open Workflow `use.catalogs` resource requires an explicitly configured
  external-catalog policy; without one it is rejected with
  `unsupported_workflow_feature` rather than fetched or silently ignored.
- Trust boundaries are deployment-controlled: alias allowlists with
  host/endpoint policy, HTTPS/TLS enforcement, no redirects, bounded streaming
  responses, and environment-only authentication. Credentials never appear in
  workflow files or catalog references.
- References use exact semantic versions with optional or required SHA-256
  digest pins. Failed or missing pins fail closed.
- Fetching uses one-shot DNS resolution with public-address validation and a
  pinned HTTP transport, so connections reach only the approved addresses while
  hostname-based TLS verification is preserved (connection-level DNS-rebinding
  resistance).
- Catalog content is cached in an isolated store with bounded revalidation;
  after a failed revalidation the runtime refuses stale or unverified content.
- Resolution happens before plan derivation across startup, child workflows,
  and schedules (resolve-before-plan ordering). `/v1/capabilities` reports the
  sanitized `features.catalogs` policy/state.
- Unsupported remote behaviors remain explicitly fail-closed; remote scripts
  and remote catalog-supplied code are never executed.

Bounded inbound A2A exposure (deployment-selected, disabled by default):

- The runtime exposes an Agent Card (`/a2a/agent.json` and
  `/.well-known/agent.json`) and a synchronous `message/send` endpoint so A2A
  clients can drive the configured workflow. Streaming (`message/stream`),
  push notifications, and persistent task objects are outside the profile.
- Two transport implementations are selectable through configuration:
  `jsonrpc` (JSON-RPC 2.0 over HTTP — the most widely deployed A2A transport,
  the default) and `http_json` (A2A HTTP+JSON). New transports may be added
  behind the same flag without workflow changes.
- The first message text part becomes the workflow input (`question`);
  the workflow output text becomes the reply's text part. Waiting, cancelled,
  and faulted workflows surface sanitized transport-specific errors.
- An optional deployment-provided bearer token guards every A2A request;
  `/v1/capabilities` reports the active `features.a2a` block.

---

# 18. Engine-Specific Capabilities Are Allowed

Do not reduce both engines to their lowest common denominator.

Example:

```json
{
  "engine": "langgraph",
  "portableProfile": "1",
  "features": {
    "streaming": true,
    "interrupt": true
  }
}
```

versus:

```json
{
  "engine": "adk",
  "portableProfile": "1",
  "features": {
    "streaming": false,
    "resume": true
  }
}
```

A workflow relying on an engine-specific capability is less portable, but it may still be valid.

---

# 19. Engine SPI

Define a small engine abstraction.

Conceptually:

```python
class WorkflowEngine:

    async def initialize(self, services):
        ...

    async def compile(
        self,
        plan: WorkflowPlan
    ) -> ExecutableWorkflow:
        ...

    async def invoke(
        self,
        workflow: ExecutableWorkflow,
        invocation: InvocationContext
    ) -> InvocationResult:
        ...

    async def resume(
        self,
        handle: ExecutionHandle,
        resume_input
    ) -> InvocationResult:
        ...

    def capabilities(self) -> EngineCapabilities:
        ...

    async def shutdown(self):
        ...
```

Do not expose ADK or LangGraph objects through this interface.

---

# 20. Engine Responsibilities

The engine owns:

```text
workflow construction
node/task lifecycle
framework execution
checkpoint integration
resume integration
framework callbacks
framework-specific agent creation
framework-specific tool registration
```

The engine does NOT own:

```text
configuration parsing
Open Workflow parsing
Open Workflow validation
jq semantics
knowledge ingestion
catalog resolution
HTTP API
runtime error contract
capability API
```

---

# 21. ADK Engine

Implementation:

```text
AdkWorkflowEngine
```

Primary orchestration mechanism:

```text
ADK 2.x Dynamic Workflows
```

Use:

```text
ctx.run_node(...)
```

for dynamically executed durable child operations.

For simple static segments, ADK graph workflows may also be used when appropriate.

Do not build the system primarily around:

```text
SequentialAgent
ParallelAgent
LoopAgent
```

These may remain useful internally in exceptional cases, but are not the architecture.

---

# 22. ADK Mapping

Conceptually:

```text
CallPlan
    ↓
ADK FunctionNode / AgentNode

ForPlan
    ↓
Dynamic workflow loop

SwitchPlan
    ↓
Dynamic condition/router

ForkPlan
    ↓
supervised concurrent child nodes

Agent call
    ↓
ADK LlmAgent

LLM call
    ↓
Model service/function node
```

Preserve ADK checkpoint and resume semantics rather than implementing a second checkpoint engine.

---

# 23. ADK IDs

Use framework-generated deterministic execution IDs by default.

Only supply custom identifiers where required for stable identity, such as dynamic collections whose order may change.

Do not manually invent IDs for every Open Workflow task.

The Execution Plan must retain the original Open Workflow task reference so logs can always map:

```text
Open Workflow Task
        ↕
Execution Plan Task
        ↕
ADK Node/Run
```

---

# 24. LangGraph Engine

Implementation:

```text
LangGraphWorkflowEngine
```

Initial preferred implementation:

```text
LangGraph Functional API
```

rather than forcing every Open Workflow construct into a static `StateGraph`.

Open Workflow itself is largely imperative:

```text
do
for
switch
fork
try
```

and LangGraph's Functional API naturally supports:

```text
entrypoints
tasks
normal Python branching
loops
parallel futures
retry
timeouts
persistence
resume
interrupts
```

Therefore it is conceptually close to ADK Dynamic Workflows.

---

# 25. LangGraph Graph API Still Has a Role

Do not prohibit `StateGraph`.

The LangGraph engine may use:

```text
Functional API
       +
StateGraph
```

where useful.

For example:

```text
complex explicit routing
subgraphs
graph visualization
specialized agent networks
```

But the initial generic Open Workflow executor should favor the Functional API because it requires less artificial conversion of imperative workflow semantics.

---

# 26. LangGraph Persistence

LangGraph execution durability should use native:

```text
checkpointers
```

and long-term application state can use:

```text
stores
```

Do not recreate them in the core runtime.

The LangGraph adapter maps the common runtime IDs onto appropriate LangGraph execution identifiers.

---

# 27. Engine-Neutral Invocation Identity

Expose common concepts:

```text
invocation_id
session_id
user_id
```

Internally maintain an engine execution handle.

Conceptually:

```text
ExecutionHandle
├── invocation_id
├── engine
├── engine_execution_reference
├── user_id
├── session_id
├── workflow_name
├── workflow_version
├── workflow_fingerprint
└── status
```

ADK may map this to an ADK invocation.

LangGraph may map it to:

```text
thread/checkpoint information
```

The public API must never require users to understand these framework-specific identifiers.

---

# 28. Workflow Mutation and Resume

Never resume a persisted workflow execution against a silently changed workflow definition.

Calculate:

```text
workflow_fingerprint
```

using the normalized workflow definition.

Persist it with the invocation.

Resume must verify:

```text
stored fingerprint == currently loaded fingerprint
```

Otherwise return:

```text
WorkflowDefinitionChanged
```

unless an explicit future migration mechanism exists.

This protects both ADK and LangGraph durable execution.

---

# 29. Default Runtime Catalog

Open Workflow Agent provides a built-in default catalog.

Initial functions:

```text
agent:1.0.0@default
llm:1.0.0@default
```

Do not modify Open Workflow to support them.

They are standard Open Workflow custom functions.

---

# 30. Agent Function

```yaml
- answer:
    call: agent:1.0.0@default
```

means:

> Invoke an agent configured by Open Workflow Agent.

The configured agent may use:

```text
model
instruction
tools
knowledge
memory
callbacks
agent reasoning/tool loop
```

Explicit input:

```yaml
- answer:
    call: agent:1.0.0@default
    with:
      input: ${ .question }
```

If omitted, transformed task input is used.

---

# 31. LLM Function

```yaml
- classify:
    call: llm:1.0.0@default
```

means:

> Invoke a model directly without invoking the autonomous agent layer.

Example:

```yaml
- classify:
    call: llm:1.0.0@default
    with:
      prompt: |
        Classify:
        ${ .request }
```

It bypasses:

```text
agent tools
knowledge tool
memory tool
agent tool-selection loop
```

unless explicitly requested by the call contract.

---

# 32. Why Agent and LLM Must Stay Separate

Example:

```text
Workflow
│
├── llm classify
│
├── deterministic switch
│
├── HTTP lookup
│
└── agent resolve
```

This allows workflows to combine:

```text
cheap LLM reasoning
deterministic orchestration
external systems
autonomous agents
```

without forcing every AI operation to become an autonomous agent.

---

# 33. Future Model Aliases

Support minimal configuration:

```yaml
model:
  provider: litellm
  name: provider/model
```

Internally normalize this to:

```text
models.default
```

Later allow:

```yaml
models:

  default:
    provider: litellm
    name: provider/model-a

  fast:
    provider: litellm
    name: provider/model-b

  reasoning:
    provider: litellm
    name: provider/model-c
```

Then:

```yaml
call: llm:1.0.0@default
with:
  model: fast
```

uses a configured alias.

Do NOT initially allow arbitrary workflow definitions to supply API endpoints or credentials.

Models should reference trusted runtime configuration.

---

# 34. Configuration Philosophy

Configuration precedence:

```text
Built-in Defaults
        ↓
YAML
        ↓
Environment Variables
```

Environment variables must override YAML.

Suggested configuration location:

```text
/config/agent.yaml
```

Override:

```text
OWA_CONFIG_FILE
```

Suggested environment convention:

```text
OWA__MODEL__NAME
OWA__KNOWLEDGE__PATH
OWA__MEMORY__ENABLED
```

---

# 35. Minimal Configuration

```yaml
model:
  name: provider/model
```

This produces:

```text
default agent
+
default one-task workflow
+
in-memory runtime state
```

---

# 36. Typical Configuration

```yaml
agent:
  name: support
  instruction: |
    Answer questions using available knowledge and tools.

model:
  provider: litellm
  name: provider/model

workflow:
  path: /config/workflow.yaml

knowledge:
  path: /knowledge
  reload:
    mode: watch

persistence:
  datasource: ${DATABASE_URL}

memory:
  enabled: auto
```

---

# 37. Typed Configuration

Use strict Pydantic models.

Conceptually:

```text
RuntimeConfig
├── AgentConfig
├── ModelConfig / ModelsConfig
├── WorkflowConfig
├── KnowledgeConfig
├── EmbeddingConfig
├── MemoryConfig
├── PersistenceConfig
├── ToolsConfig
├── ServerConfig
└── ObservabilityConfig
```

Unknown properties should fail.

Example:

```yaml
modle:
```

must produce a configuration error rather than being ignored.

---

# 38. Model Abstraction

The external model contract is common.

Framework-specific model objects are not.

Therefore:

```text
ModelConfig
     │
     ├── ADK ModelAdapter
     │
     └── LangGraph ModelAdapter
```

The first generic provider should be:

```text
LiteLLM
```

because it allows broad provider coverage.

Native engine/model integrations may later be added.

Do not scatter provider conditionals throughout the code.

---

# 39. Knowledge

Knowledge means externally supplied information.

Examples:

```text
documents
manuals
policies
Markdown
PDF
JSON
YAML
```

It is not memory.

---

# 40. Session, Memory and Knowledge

Maintain these concepts separately.

```text
Knowledge
    externally supplied information

Session
    current conversation/execution state

Memory
    information carried across interactions
```

Never collapse them into one generic "RAG storage" abstraction.

---

# 41. Automatic Knowledge Activation

If:

```text
/knowledge
```

exists and contains supported documents:

```text
KnowledgeService = enabled
```

If it does not exist and no sources are configured:

```text
KnowledgeService = disabled
```

The user should not need:

```yaml
knowledge:
  enabled: true
```

for the common mounted-folder case.

---

# 42. Knowledge Startup Lifecycle

```text
Discover
   ↓
Hash
   ↓
Compare Manifest
   ↓
Parse Changed Documents
   ↓
Chunk
   ↓
Embed
   ↓
Index
```

Do not embed unchanged documents after every restart.

Persist:

```text
file path
file hash
parser version
chunking version
embedding model/version
index timestamp
```

Deleted files must delete their indexed chunks.

---

# 43. Knowledge Reload

Support:

```text
startup
manual
watch
```

Default:

```text
startup
```

Manual:

```text
POST /v1/admin/knowledge/reload
```

Watch mode should use periodic fingerprint reconciliation as the reliable baseline because mounted/container filesystems may not deliver filesystem events consistently.

A filesystem watcher may optimize this but must not be the only mechanism.

---

# 44. Embedding Abstraction

Define:

```text
EmbeddingProvider
```

The knowledge implementation must not know which embedding model is used.

Resolution strategy:

```text
explicit embedding config
        ↓
configured embedding provider
        ↓
packaged local default
```

The goal is ultimately:

> Mounting `/knowledge` should work without requiring another paid service.

Before Milestone Knowledge is considered complete, select and pin a small permissively licensed local embedding model suitable for CPU execution.

Do not make the model choice part of the KnowledgeService API.

---

# 45. Vector Store

Define:

```text
VectorStore
```

For the first implementation use an embedded persistent implementation.

Do not require:

```text
Qdrant
Milvus
OpenSearch
```

to run a simple container.

Possible first implementation:

```text
SQLite metadata
+
local vectors
+
NumPy similarity
```

Later:

```text
pgvector
Qdrant
OpenSearch
Milvus
```

can implement the same contract.

---

# 46. Knowledge as Agent Tool

When knowledge is enabled, automatically register:

```text
search_knowledge
```

with the configured agent.

The ADK engine wraps it as an ADK tool.

The LangGraph engine wraps the same underlying service as an appropriate LangGraph/LangChain tool.

The retrieval implementation itself remains common.

---

# 47. Persistence

A configured datasource activates persistent runtime behavior.

Example:

```yaml
persistence:
  datasource: postgresql://...
```

This datasource can provide storage for:

```text
runtime invocation metadata
memory
knowledge metadata
engine persistence
```

but each subsystem must use separate tables/namespaces.

Do not make one engine read another engine's checkpoint representation.

---

# 48. Engine-Owned Durable State

This is an explicit rule.

```text
ADK
 └── ADK resumability/session mechanisms

LangGraph
 └── LangGraph checkpointer/store mechanisms
```

The common runtime owns:

```text
public invocation identity
workflow fingerprint
status
metadata
```

but does not replace framework checkpoint functionality.

---

# 49. Memory

Memory is a common semantic capability implemented through a common interface:

```text
MemoryService
├── add
├── search
└── delete
```

Initial behavior:

```text
No datasource
    ↓
in-memory

Datasource
    ↓
persistent
```

Both engines expose the memory service to their configured agents.

---

# 50. Memory Is Not Engine Checkpointing

Do not confuse:

```text
LangGraph Checkpointer
ADK workflow events/session
```

with:

```text
Agent long-term memory
```

Checkpointing exists to resume execution.

Memory exists so the agent can retrieve prior information.

They have different lifecycles and retention policies.

---

# 51. Tools

There are two different meanings of "tool."

## Agent tool

Configured:

```yaml
tools:
  - type: mcp
```

means:

> The autonomous agent may decide whether to use the tool.

## Workflow call

```yaml
- lookup:
    call: mcp
```

means:

> The workflow explicitly performs an MCP operation.

These must remain separate.

---

# 52. Common Protocol Services

Where practical, implement protocol behavior in the common core:

```text
HTTP
MCP
A2A
OpenAPI
```

Then expose wrappers to:

```text
ADK agents
LangGraph agents
Open Workflow calls
```

Example:

```text
                 McpClient
                    │
        ┌───────────┼───────────┐
        │           │           │
      OWS call    ADK tool   LangGraph tool
```

This avoids implementing MCP three times.

---

# 53. Agent Construction

Each engine owns its agent adapter.

Conceptually:

```text
AgentFactory
    │
    ├── AdkAgentFactory
    │
    └── LangGraphAgentFactory
```

Both receive the same:

```text
AgentSpec
ModelSpec
Tools
Knowledge Retriever
Memory
```

and produce framework-native agent objects.

---

# 54. HTTP Invocation API

Primary endpoint:

```text
POST /v1/invoke
```

Example:

```json
{
  "user_id": "u123",
  "session_id": "s456",
  "input": "How can I renew my license?"
}
```

`input` may be arbitrary JSON.

If `session_id` is omitted, generate one and return it.

`user_id` is application identity/correlation information.

It must not automatically be treated as authenticated security identity.

---

# 55. Invocation Response

```json
{
  "invocation_id": "...",
  "session_id": "...",
  "status": "completed",
  "output": {}
}
```

The portable lifecycle state set includes:

```text
running
waiting
completed
faulted
cancelled
```

Keep these aligned with Open Workflow lifecycle concepts where practical.

---

# 56. Resume API

```text
POST /v1/invocations/{invocation_id}/resume
```

The common API retrieves the:

```text
ExecutionHandle
```

and delegates resume to the selected engine.

The caller never provides:

```text
ADK run IDs
LangGraph thread IDs
checkpoint IDs
```

directly.

Cancellation is exposed as the minimal engine-neutral operation:

```text
POST /v1/invocations/{invocation_id}/cancel
```

Cancellation is idempotent for terminal invocations and is valid while an invocation is
`running` or `waiting`. The public operation accepts only the common invocation identity;
native ADK run identifiers, LangGraph thread/checkpoint data, and checkpoint payloads are
never required or returned.

---

# 57. Streaming

Streaming is NOT part of the mandatory portable v1 profile.

Reason:

Framework capabilities currently differ.

Provide:

```text
streaming: true|false
```

through `/v1/capabilities`.

A future streaming endpoint can be optional.

Do not compromise the basic portable API by forcing all engines to mimic streaming semantics they do not naturally provide.

---

# 58. Capabilities Endpoint

```text
GET /v1/capabilities
```

Example:

```json
{
  "runtime": "open-workflow-agent",
  "runtimeVersion": "0.1.0",
  "engine": "adk",
  "workflowDsl": "1.0.3",
  "portableProfile": "1",
  "tasks": [
    "do",
    "call",
    "set",
    "switch",
    "for",
    "fork",
    "try",
    "wait",
    "listen",
    "emit",
    "raise"
  ],
  "functions": [
    "agent:1.0.0@default",
    "llm:1.0.0@default"
  ],
  "features": {
    "resume": true,
    "cancellation": true,
    "waiting": true,
    "events": {
      "emit": true,
      "listen": true,
      "durable": false
    },
    "cloudEvents": {
      "lifecycle": true,
      "specversion": "1.0",
      "delivery": "bounded_snapshot",
      "durable": false
    },
    "scheduling": {
      "after": true,
      "every": true,
      "cron": false,
      "on": false,
      "durable": true,
      "owner": "single_runtime"
    },
    "streaming": false
  }
}
```

---

# 59. Expression Language

Open Workflow's default runtime expression language is `jq`.

Implement:

```text
ExpressionEvaluator
└── JqExpressionEvaluator
```

Do not replace it with:

```text
JMESPath
Python eval
custom syntax
```

for the portable profile.

Only expressions defined in trusted workflow definitions may be evaluated.

Never evaluate apparent expression syntax arriving through user/workflow input data.

---

# 60. Open Workflow Data Semantics

The common core owns:

```text
workflow input validation
workflow input.from
task input validation
task input.from
task execution
task output.as
task export.as
workflow context
workflow output.as
```

These semantics must not be delegated independently to each engine.

The Execution Plan and common data services ensure identical behavior.

---

# 61. Side Effects and Resume

Any operation with side effects must be treated as potentially retryable/re-executable.

Examples:

```text
payment
email
database update
external API command
A2A action
MCP write tool
```

Whenever possible support:

```text
idempotency keys
deduplication
operation identifiers
```

Do not assume "checkpointed" means "exactly once."

---

# 62. Workflow Security Model

Workflow files are trusted deployment artifacts.

User input is not trusted.

Executable operations remain disabled by default and are enabled only through
the deployment security profile:

```text
run.shell       sandbox.enabled + sandbox.allow_shell
run.script      sandbox.enabled + sandbox.script_runtimes
run.container   sandbox.enabled + sandbox.backend = docker | kubernetes
```

External catalogs are also disabled by default and require an explicitly
configured deployment trust policy (see the bounded external-catalog profile in
section 17).

Default catalog:

```text
local runtime-provided functions only
```

This prevents arbitrary remote catalogs from becoming an uncontrolled code/integration mechanism.

---

# 63. Network Security

All protocol clients must support:

```text
timeouts
TLS verification
maximum response size
redirect policy
authentication abstraction
```

Later add configurable:

```text
host allowlists
network policies
egress restrictions
```

Avoid accepting arbitrary credentials directly inside ordinary workflow files when deployment secrets can be referenced instead.

---

# 64. Observability

Every workflow task should be traceable through:

```text
invocation_id
session_id
workflow name
workflow version
task name
task reference
engine
engine node/run identifier
duration
status
```

The canonical task reference is the Open Workflow task reference.

Example:

```text
/do/2/classify
```

This gives a stable identifier independent of execution engine.

---

# 65. Lifecycle Events

Create a common internal event model:

```text
WorkflowStarted
WorkflowCompleted
WorkflowFaulted
WorkflowWaiting
WorkflowResumed
WorkflowCancelled

TaskStarted
TaskCompleted
TaskFaulted
TaskRetried
TaskProgress
TaskWaiting
TaskCancelled
EventEmitted
EventReceived
```

Initially these events can drive:

```text
structured logging
metrics
tests
```

The optional lifecycle CloudEvents boundary exposes these events as CloudEvents
1.0 JSON batches through `GET /v1/events/lifecycle`. It is bounded, in-memory,
non-streaming, and non-durable.

This provides observability independent of ADK/LangGraph's different event formats.

Portable workflow eventing uses the official event properties (`id`, `source`,
`type`, `time`, `subject`, content metadata, and `data`) in a common envelope.
External injection is available through:

```text
POST /v1/events
{ "event": { "type": "...", "data": {} } }
```

The endpoint and `emit` task publish to the selected process-local runtime; a
`listen` task consumes one matching event. The API does not expose broker,
checkpoint, or engine-native identifiers.

---

# 66. Error Contract

Create engine-neutral errors:

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

Framework exceptions must be translated to this contract at the engine boundary.

Preserve the original error internally for diagnostics.

---

# 67. Repository Structure

Recommended monorepo:

```text
open-workflow-agent/
│
├── README.md
├── docs/
│
├── core/
│   ├── pyproject.toml
│   └── src/open_workflow_agent/
│       ├── config/
│       ├── workflow/
│       │   ├── loading/
│       │   ├── validation/
│       │   ├── normalization/
│       │   ├── plan/
│       │   ├── expressions/
│       │   └── lifecycle/
│       ├── catalog/
│       ├── models/
│       ├── knowledge/
│       ├── memory/
│       ├── protocols/
│       ├── invocation/
│       ├── api/
│       └── engine/
│
├── engines/
│   ├── adk/
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   └── src/
│   │
│   └── langgraph/
│       ├── pyproject.toml
│       ├── uv.lock
│       └── src/
│
├── runtime-catalog/
│   └── functions/
│       ├── agent/
│       │   └── 1.0.0/
│       │       └── function.yaml
│       └── llm/
│           └── 1.0.0/
│               └── function.yaml
│
├── resources/
│   └── open-workflow/
│       └── 1.0.3/
│           └── workflow.yaml
│
├── docker/
│   ├── Dockerfile.adk
│   └── Dockerfile.langgraph
│
├── examples/
│
└── tests/
    ├── contract/
    ├── core/
    ├── adk/
    ├── langgraph/
    └── e2e/
```

---

# 68. Separate Dependency Locks

ADK and LangGraph dependencies must be independently lockable.

Do not require both dependency graphs to resolve in the same runtime environment.

Therefore:

```text
engines/adk/uv.lock
engines/langgraph/uv.lock
```

This prevents an upgrade in LangGraph from blocking the ADK image or vice versa.

The core package should maintain minimal framework-independent dependencies.

---

# 69. Docker Images

Build:

```text
<org>/open-workflow-agent-adk:<project-version>

<org>/open-workflow-agent-langgraph:<project-version>
```

Both expose:

```text
/config
/knowledge
/data
port 8080
```

Both accept exactly the same public configuration.

---

# 70. OpenShift/Kubernetes Compatibility

Containers must:

```text
run non-root
not require fixed UID
write only to configured writable paths
support read-only root filesystem where practical
expose readiness/liveness
handle SIGTERM gracefully
externalize all state
```

No runtime package installation during startup.

---

# 71. Shared Contract Test Suite

This is one of the most important parts of the project.

Create:

```text
tests/contract
```

containing workflow fixtures independent of execution engine.

For example:

```text
minimal-agent.yaml
llm-call.yaml
sequence.yaml
set.yaml
switch.yaml
for.yaml
fork.yaml
data-transform.yaml
error.yaml
```

Each engine executes the same fixtures.

Expected output must be identical unless an engine-specific capability is explicitly being tested.

---

# 72. Example Contract Test

Conceptually:

```python
@pytest.mark.parametrize(
    "engine",
    ["adk", "langgraph"]
)
async def test_switch_workflow(engine):
    result = await run_fixture(
        engine,
        "switch.yaml"
    )

    assert result == expected
```

This is how portability is proved.

Not through documentation claims.

---

# 73. Deterministic Test Model

No CI test should require a paid API.

Create:

```text
FakeModel
```

with deterministic responses.

It must support enough functionality to test:

```text
simple response
structured response
tool request
controlled failure
controlled retry
```

Provide engine adapters for the same fake model semantics.

---

# 74. Test Layers

## Core tests

Test:

```text
configuration
schema validation
normalization
execution plan
jq expressions
catalog resolution
knowledge
memory
protocols
```

## Engine tests

Test:

```text
plan compilation
agent invocation
checkpointing
resume
framework errors
```

## Contract tests

Test equivalent semantics on every engine.

## E2E tests

Build and run actual containers.

---

# 75. Open Workflow CTK

The Open Workflow CTK should eventually become a major compatibility gate.

However:

```text
Portable Profile v1
```

is intentionally a subset.

Do not advertise:

```text
"Open Workflow 1.0.3 compliant runtime"
```

until the appropriate CTK scenarios pass.

Until then use wording such as:

```text
Open Workflow 1.0.3 based runtime
```

or:

```text
supports OWA Portable Profile v1
```

---

# 76. Implementation Strategy

Do not implement ADK and LangGraph simultaneously from day one.

The safest sequence is:

```text
Core contract
    ↓
ADK vertical slice
    ↓
Portable workflow semantics
    ↓
LangGraph implementation
    ↓
Cross-engine parity
```

This avoids prematurely abstracting hypothetical framework differences.

---

# 77. Milestone 0 — Core Contracts

Implement only:

```text
configuration models
Open Workflow loader
official schema validation
default workflow generation
catalog definitions
execution plan models
normalizer
engine SPI
runtime result/error models
contract test fixtures
```

No real engine execution yet.

Acceptance:

```text
workflow.yaml
   ↓
validated WorkflowPlan
```

and default configuration produces an equivalent generated plan.

---

# 78. Milestone 1 — ADK Vertical Slice

Implement:

```text
ADK engine package
model adapter
agent factory
agent catalog function
ADK dynamic execution
/v1/invoke
health
FakeModel
Dockerfile.adk
E2E test
```

Only one task needs to work initially:

```text
agent:1.0.0@default
```

Acceptance:

```text
model configuration
       ↓
default workflow
       ↓
ADK runtime
       ↓
FakeModel
       ↓
deterministic response
```

---

# 79. Milestone 2 — Portable Workflow Profile

Implement common semantics:

```text
do
call
set
switch
for
fork
jq
input/from
output/as
export/as
http
llm
```

ADK must pass all portable contract tests.

---

# 80. Milestone 3 — LangGraph Engine

Implement:

```text
LangGraph engine package
Functional API executor
model adapter
agent adapter
checkpoint integration
Dockerfile.langgraph
```

Run the exact contract fixtures already passing on ADK.

Acceptance:

```text
ADK result == LangGraph result
```

for all Portable Profile v1 workflows.

---

# 81. Milestone 4 — Knowledge

Implement:

```text
folder discovery
parsers
manifest
chunking
embedding provider
embedded vector store
search_knowledge
reload endpoint
watch/reconciliation
```

Test against both images.

Knowledge implementation itself must remain in core.

Only tool wrapping is engine-specific.

---

# 82. Milestone 5 — Persistence and Memory

Implement:

```text
InvocationStore
ExecutionHandle
workflow fingerprints
persistent memory
ADK durable adapter
LangGraph persistent checkpointer
resume API
```

Acceptance includes:

```text
execute
stop container
restart
resume
complete
```

for both engines where supported.

---

# 83. Milestone 6 — Agent Tools

Implement:

```text
MCP
OpenAPI
```

as agent tools.

Configure them externally.

No image rebuild is required when adding/removing configured tools.

---

# 84. Milestone 7 — Extended Workflow Calls

Implement common protocol execution for:

```text
MCP
A2A
OpenAPI
```

Then expand Open Workflow support:

```text
try
retry
timeout
wait
raise
```

---

# 85. Later Milestones

Already delivered from this list (see section 17 and `PROJECT.md`):

```text
external catalogs   bounded deployment-trusted profile implemented
HITL                bounded durable approval state/replay implemented
additional engines  Microsoft Agent Framework native adapter merged as an
                    optional package; production status still deferred
```

Delivered as a bounded slice (see section 17):

```text
A2A exposure        inbound bounded profile: Agent Card + synchronous
                    message/send with selectable transports (jsonrpc
                    default, http_json), behind deployment configuration
```

Still deferred:

```text
A2A conformance     message/stream, push notifications, task objects,
                    full Agent Card conformance
streaming           general portable output streaming beyond bounded
                    lifecycle SSE
```

Remaining deferred-engine work:

```text
OpenAI Agents SDK
other graph/workflow frameworks
```

No architectural redesign should be needed to introduce another engine if the SPI is correctly maintained.

---

# 86. Non-Goals

Do not build initially:

```text
visual workflow designer
custom workflow DSL
BPMN engine
distributed scheduler
custom LLM framework
custom MCP protocol
custom A2A protocol
enterprise vector database
multi-tenant management UI
arbitrary Python plugins
arbitrary shell execution
```

---

# 87. Core Architectural Boundaries

The following dependency direction must be enforced:

```text
API
 ↓
Core
 ↓
Engine SPI
 ↑
ADK / LangGraph
```

Never:

```text
Core imports ADK
Core imports LangGraph
```

Engine packages depend on core.

Core never depends on engines.

---

# 88. Most Important Portability Rule

Framework-native state must never become the application contract.

Bad:

```yaml
langgraph:
  thread_state: ...
```

Bad:

```yaml
adk:
  agent_run_id: ...
```

Good:

```yaml
agent:
model:
workflow:
knowledge:
memory:
tools:
```

This allows the same deployment configuration to move between runtime engines.

---

# 89. Most Important Workflow Rule

Open Workflow is the authoring model.

The internal Execution Plan exists only to ensure:

```text
consistent normalization
consistent semantics
multiple engine compilation
better testing
better diagnostics
```

It must never evolve into a competing workflow specification.

---

# 90. Most Important Runtime Rule

Use framework capabilities instead of rebuilding them.

For ADK:

```text
workflow runtime
dynamic nodes
agents
resume
sessions
```

For LangGraph:

```text
tasks
entrypoints
checkpointers
stores
interrupts
```

Open Workflow Agent provides portability and composition.

It should not become another full durable workflow engine underneath those frameworks.

---

# 91. Most Important Testing Rule

A feature is not portable because two adapters claim to support it.

A feature is portable only when:

```text
same workflow fixture
       +
same inputs
       ↓
ADK
       ↓
expected output

same workflow fixture
       +
same inputs
       ↓
LangGraph
       ↓
same expected output
```

Contract tests define actual runtime portability.

---

# 92. Instructions for AI Development Agents

Every coding task must include this project definition as architectural context.

Coding agents must follow these rules:

1. Do not redesign public contracts without explicit instruction.

2. Do not introduce another workflow DSL.

3. Do not modify the Open Workflow schema.

4. Do not expose the internal Execution Plan publicly.

5. Core must not import ADK or LangGraph.

6. Framework-specific code belongs under its engine package.

7. Do not implement framework checkpointing in core.

8. Open Workflow data semantics belong in core.

9. `jq` is the default expression language.

10. Missing workflow means generate the default one-task workflow.

11. All invocations execute through workflow execution.

12. `agent` and `llm` are different catalog functions.

13. Keep Knowledge, Memory, Session and Checkpointing separate.

14. Do not require a paid model in tests.

15. Add tests before marking a feature complete.

16. Run the existing contract suite after every engine change.

17. Do not silently ignore unsupported Open Workflow features.

18. Do not add dependencies without a demonstrated requirement.

19. Do not install dependencies dynamically during container startup.

20. Keep ADK and LangGraph dependency locks separate.

21. Update capabilities when runtime support changes.

22. Never log credentials or secrets.

23. Preserve Open Workflow task references through every runtime layer.

24. Verify framework API usage against the pinned framework version before implementation.

25. Complete only the requested milestone. Do not proactively implement later milestones.

---

# 93. Architectural Decisions Summary

## ADR-001

**Every invocation is a workflow.**

No separate agent execution path.

## ADR-002

**Open Workflow Specification is the external workflow language.**

No proprietary workflow DSL.

## ADR-003

**Missing workflow generates an implicit one-task workflow.**

The generated workflow calls:

```text
agent:1.0.0@default
```

## ADR-004

**AI-native Open Workflow operations use the default catalog.**

Initial:

```text
agent
llm
```

No Open Workflow schema fork.

## ADR-005

**The project is framework-neutral.**

ADK and LangGraph are runtime engines.

## ADR-006

**One repository, multiple engine implementations.**

Avoid duplicated projects.

## ADR-007

**Separate Docker images per engine.**

Avoid dependency pollution.

## ADR-008

**A canonical internal Execution Plan is used.**

This becomes justified by multiple engine implementations.

## ADR-009

**The Execution Plan is not an external DSL.**

Open Workflow remains authoritative.

## ADR-010

**Open Workflow semantics live in core.**

Framework-specific execution lives in engine adapters.

## ADR-011

**Durability remains engine-native.**

Do not build another checkpoint engine.

## ADR-012

**Portability is demonstrated through shared contract tests.**

Not assumed.

## ADR-013

**Capabilities are explicit.**

The engines may have different optional features.

## ADR-014

**Knowledge is common core functionality.**

Engine adapters only expose it as native tools.

## ADR-015

**Knowledge, memory, session and execution persistence are distinct.**

Do not collapse them.

---

# 94. Final Architecture

```text
                     OPEN WORKFLOW AGENT
                             │
                  Stable Public Contracts
                             │
             ┌───────────────┼────────────────┐
             │               │                │
           Config       Open Workflow        API
                             │
                             ▼
                     Validate + Normalize
                             │
                             ▼
                   Canonical Execution Plan
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
        ADK ENGINE                   LANGGRAPH ENGINE
              │                             │
       Dynamic Workflow              Functional API
       Graph Runtime                  Checkpointer
       ctx.run_node                   Tasks/Entrypoint
       ADK Agents                     Agent/Graph
              │                             │
              └──────────────┬──────────────┘
                             │
                       Common Services
                             │
       ┌────────────┬────────┼───────────┬──────────┐
       ▼            ▼        ▼           ▼          ▼
     Models      Knowledge  Memory      Tools     Protocols
       │            │        │           │          │
    LiteLLM      Embedding  Store      MCP/etc. HTTP/MCP/A2A
```

---

# 95. Final Project Definition

Open Workflow Agent is:

> **A lightweight, configuration-driven, model-agnostic agent and workflow platform that executes Open Workflow definitions through interchangeable agent-runtime engines such as ADK and LangGraph.**

Its most important characteristics are:

```text
one external contract
one repository
multiple engines
separate Docker images
Open Workflow as the DSL
default workflow when none is supplied
agent + llm runtime catalog functions
externalized configuration
automatic knowledge ingestion
optional persistent memory
engine-native durability
cross-engine contract testing
```

The architecture is intentionally designed so that:

```text
workflow.yaml
agent.yaml
/knowledge
```

remain unchanged when deployment changes from:

```text
open-workflow-agent-adk
```

to:

```text
open-workflow-agent-langgraph
```

provided the workflow uses capabilities in the common portable profile.

That portability is the central value of the project.
