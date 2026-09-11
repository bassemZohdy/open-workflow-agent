# Architecture Overview

This document provides a concise overview of the Open Workflow Agent architecture for new contributors. For the full product contract, see `Project Definition.md`.

## Runtime Pipeline

```text
Load workflow (YAML/JSON or path)
  -> Open Workflow 1.0.3 schema validation
  -> Portable Profile capability gate
  -> Normalize to canonical execution plan
  -> Execute on selected engine (ADK or LangGraph)
```

The execution plan is typed, immutable, fingerprinted, and internal. It is never exposed to clients.

## Module Structure

```text
core/src/open_workflow_agent/
├── api.py              # FastAPI HTTP endpoints
├── a2a.py              # Inbound A2A protocol server
├── a2a_streaming.py    # A2A streaming/resubscription
├── a2a_tasks.py        # A2A Task projection
├── approvals.py        # Human-in-the-loop approvals
├── catalog.py          # Function catalog and model abstractions
├── config.py           # Runtime configuration (Pydantic)
├── engine.py           # Engine SPI and portable reference engine
├── errors.py           # Error hierarchy
├── events.py           # Event bus
├── external_catalog.py # External workflow catalog resolver
├── knowledge.py        # RAG knowledge service
├── lifecycle.py        # Invocation lifecycle management
├── logging_config.py   # Structured logging support
├── memory.py           # Conversation memory service
├── observability.py    # Lifecycle events and CloudEvents
├── persistence.py      # Invocation state persistence
├── protocols.py        # Protocol clients (HTTP/MCP/A2A/OpenAPI)
├── sandbox/            # Sandbox execution backends
│   ├── backends/       # Internal, Docker, Kubernetes
│   ├── contract.py     # Abstract sandbox interface
│   ├── executor.py     # Workflow executor
│   ├── manager.py      # Sandbox orchestration
│   └── validation.py   # Capability validation
├── scheduling.py       # Workflow scheduling
├── security.py         # Security profiles and authorization
├── security_headers.py # Response security headers
├── server.py           # ASGI entry point
├── services.py         # Composition root (DI)
├── storage.py          # Datasource/namespace resolution
├── streaming.py        # SSE lifecycle streaming
├── tools.py            # Tool registry
├── traffic_policy.py   # Rate limiting and concurrency
├── workflow.py         # Workflow loading, validation, execution
└── workflow_catalog.py # Internal workflow catalog
```

## Key Abstractions

### WorkflowEngine (Engine SPI)

```python
class WorkflowEngine:
    async def invoke(self, plan, handle, input) -> InvocationResult
    async def resume(self, handle, input, plan) -> InvocationResult
    async def cancel(self, handle) -> InvocationResult
    def capabilities(self) -> EngineCapabilities
```

Production engines: `PortableWorkflowEngine` (reference), ADK, LangGraph.

### RuntimeServices (Composition Root)

Wires together: model, catalog, event bus, knowledge, memory, persistence, sandbox, protocols, tools, approvals, workflow catalog, scheduling.

### Security Profiles

Named profiles with environment-only secrets:

- `bearer` - Bearer token authentication
- `api_key` - API key in custom header
- `oauth2_client_credentials` - OAuth2 client credentials flow
- `mtls` - Mutual TLS with client certificates

### Sandbox Backends

- **Internal** - Controlled child-process sandbox (default when enabled; not hard isolation)
- **Docker** - Restricted Docker container via controller socket
- **Kubernetes** - Restricted K8s pod via controller sidecar

## Data Flow

```text
Client Request
  -> API Middleware (auth, rate limit, security headers, CORS)
  -> FastAPI Route
  -> WorkflowEngine.invoke(plan, handle, input)
  -> WorkflowExecutor.execute(plan, input, metadata)
  -> Task execution (call agent, llm, protocol, etc.)
  -> InvocationResult (output, status)
  -> Response to client
```

## Configuration

Configuration is loaded from YAML with environment variable overrides:

```text
/config/agent.yaml (default)
OWA_CONFIG_FILE (env override)
OWA__SECTION__KEY (env override for nested keys)
```

All configuration is validated with Pydantic `StrictModel` (extra fields forbidden).

## Testing Structure

```text
tests/
├── core/           # Core unit tests (45 files)
├── contract/       # Cross-engine behavioral contract tests
├── ctk/            # Open Workflow CTK subset
├── adk/            # ADK-specific tests
├── langgraph/      # LangGraph-specific tests
├── agent_framework/# Agent Framework tests
├── e2e/            # End-to-end API tests
└── ci/             # CI acceptance scripts
```

## Extension Points

1. **Custom Catalog Functions** - Register functions in the catalog for workflow tasks
2. **Engine Adapters** - Implement `WorkflowEngine` SPI for new runtimes
3. **Sandbox Backends** - Implement `SandboxBackend` for new execution environments
4. **Protocol Clients** - Extend `ProtocolServices` for new protocols
5. **Security Profiles** - Add new profile types in `security.py`

## Key Commands

```bash
# Install dependencies
uv sync --locked --extra knowledge

# Run tests
uv run pytest tests/core/ -q

# Run linter
uv run ruff check .

# Run type checker
uv run mypy --package open_workflow_agent --package open_workflow_agent_adk --package open_workflow_agent_langgraph --package open_workflow_agent_agent_framework

# Build package
uv build
```

## Further Reading

- `Project Definition.md` - Full architecture/product contract
- `PROJECT.md` - Verified implementation state
- `docs/sandbox-execution.md` - Sandbox architecture
- `docs/protocol-security-decisions.md` - Security design decisions
- `docs/a2a-streaming-evaluation.md` - A2A implementation details
- `docs/custom-catalog-functions.md` - Custom catalog authoring and trust policy
