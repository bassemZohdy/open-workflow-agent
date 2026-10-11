:warning: **Superseded.** The `agent:` task key was removed in favor of `task.metadata.adk.agent`.
This ADR is kept as historical context.

# ADR 0001: Task-level agent characteristics

- Status: Superseded — agent config now lives in `task.metadata.adk.agent`.
- Date: 2026-08-10

## Historical decision

Agent characteristics were originally represented by a dedicated `agent` key on a
task:

```yaml
do:
  - summarize:
      agent:
        model: gemini-2.5-flash
        instruction: Summarize the input.
      call: function
```

The extension was accepted alongside the OpenWorkflow v1.0.3 task shape. The
upstream schema was authoritative for every non-extension field; validation
removed `agent` before applying that schema and validated the extension with this
project’s Pydantic model.

## Current encoding

To keep documents valid OpenWorkflow v1.0.3 for other implementors, the agent
configuration moved into the metadata container:

```yaml
do:
  - summarize:
      metadata:
        adk:
          agent:
            model: gemini-2.5-flash
            instruction: Summarize the input.
      call: function
```

Both `task.metadata` and `document.metadata` allow additional properties in the
upstream schema, so other implementors parse and ignore the `adk` block.

## Rationale

The task is the unit translated into an ADK agent, so colocating model,
instruction, tools, and generation settings keeps workflow intent discoverable.
The metadata container preserves OpenWorkflow compatibility without needing a
separately named extension object.

## Consequences

Documents using the legacy `agent:` task key are rejected with a migration hint.
The project validates the `metadata.adk` payload with Pydantic and leaves the
container intact for upstream schema validation.
