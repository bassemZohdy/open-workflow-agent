# ADR 0006: Pluggable memory backends

## Decision

Memory is configured through named `MemoryConfig` entries and resolved to an
ADK `BaseMemoryService`. The implementation ships deterministic local
in-memory and JSON-file backends, Redis for shared low-latency storage,
SQLAlchemy/asyncpg for PostgreSQL, and ADK's native Vertex Memory Bank service.

Local and remote adapters use the same `MemoryEntry` representation. Redis and
PostgreSQL store serialized entries keyed by application and user; the current
query implementation performs token matching so deployments can replace it
with vector search without changing the workflow document.

## Trade-offs

- File storage is convenient for development but is not a multi-process lock or
  a semantic index.
- Redis avoids a schema migration but requires retention/eviction policy from
  the deployment.
- PostgreSQL provides durable ownership and migrations through SQLAlchemy but
  requires an asyncpg-capable URL and table privileges.
- Vertex offers managed semantic memory but requires the optional ADK GCP extra
  and an agent engine ID.

Remote round-trip tests run in CI with Redis and PostgreSQL Testcontainers;
they are opt-in locally via `WORKFLOW_RUN_INTEGRATION_TESTS=1`.
