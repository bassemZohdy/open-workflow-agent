# Embedded deployment and bootstrap boundaries

The staged source build starts with one selected engine container, embedded
SQLite invocation/knowledge/checkpoint stores and the in-process event bus.
No external database or broker is required. With no model binding, the
`fake/default` namespace selects the deterministic fake model, so the generated
one-task agent workflow runs without provider credentials. The fake model echoes
input for local validation; configure a real provider for model reasoning.
Published release tags keep their own versioned behavior; these changed defaults
are qualified against the recorded source commit before a replacement release.

For repository builds, start one engine:

```bash
docker compose --profile langgraph up --build
# or
docker compose --profile adk up --build
```

Each selection starts one runtime and persists embedded data in its engine's
named volume. The base Compose file has no PostgreSQL dependency. Keep the data
volume to preserve invocation records and native checkpoints across restarts.
Memory persistence remains explicitly configurable; events are not yet durable.
External sandbox controllers are opt-in deployment components.

For optional local PostgreSQL, add the bootstrap override:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml --profile langgraph up --build
```

The override adds PostgreSQL, its durable volume, the datasource binding and
health ordering. Existing deployments that used Compose's old implicit database
must include this override with their original project/volume names to keep
using that database; starting the base file selects embedded storage instead.
No data is migrated or deleted automatically. To use an already managed external
database, inject `OWA__PERSISTENCE__DATASOURCE` through deployment secrets.

Bootstrap settings remain in `/config/agent.yaml` (or `OWA_CONFIG_FILE`) and
`OWA__...` environment variables, with defaults < file < environment precedence.
Workflow definitions remain official Open Workflow YAML/JSON. The existing
`workflow.path`, inline `workflow.definition` and catalog registration fields
select definitions; they do not define an alternative orchestration language.

| Workflow/task definition | Separate bootstrap/runtime binding |
| --- | --- |
| Task calls/arguments, jq transforms, conditions, transitions, loops, output/export and task error policies | Installed providers/dependencies, default model/agent bindings, storage locations, endpoints, credential references, ingress/security and sandbox policy |
| Original task references and workflow document identity/version | Engine image selection and engine-private checkpoint configuration |
| Versioned document content determines the common fingerprint | Changing provider/storage bindings does not rewrite the workflow fingerprint |

A model-only bootstrap still works:

```yaml
model:
  name: openai/<model-name>
workflow:
  path: /config/workflow.yaml
```

When `model.provider` is omitted, `fake/` names select the fake adapter and other
model identifiers select LiteLLM. Explicit provider selection is preserved.
Provider credentials resolve from environment/deployment secrets. Bootstrap
validation errors omit rejected values and YAML source, including tracebacks.
Unknown bootstrap properties remain rejected. Proposed configuration inventory
fields, native metadata extensions and independent JS runtime selection remain
staged work; their presence in the tracker does not activate them.

Evidence: [shared bootstrap/default/restart tests](../tests/contract/test_embedded_bootstrap.py),
[configuration and sanitized error checks](../tests/core/test_config.py),
[no-configuration runtime image acceptance](../tests/ci/container_embedded_defaults_acceptance.sh),
and the embedded/optional Compose validation in CI.
