# Agent extension versioning

The extension schema is versioned independently from the OpenWorkflow DSL. The
current extension is `2.0.0` and encodes ADK configuration inside
OpenWorkflow-compatible metadata containers:

- Task-level agent configuration lives in `task.metadata.adk`.
- Project-level model, provider, and memory registries live in
  `document.metadata.adk`.

The major version changed to `2.0.0` because the encoding moved from direct task
and `use.*` properties (`agent:`, `self_heal:`, `use.models:`, `use.providers:`,
`use.memories:`) to the `metadata.adk` container. Additive fields use a minor
version; removed or incompatible fields require a major version and a migration
note.
