# Agent-characteristics extension

This project extends OpenWorkflow v1.0.3 with ADK-specific configuration that is
stored inside OpenWorkflow-compatible metadata containers. The same YAML file
is valid OpenWorkflow v1.0.3 for other implementors (e.g., SonataFlow): ADK
configuration lives under `metadata.adk`, and because OpenWorkflow's
`task.metadata` and `document.metadata` both allow arbitrary additional
properties, other implementors can parse and ignore the ADK block without error.

## OpenWorkflow-compatible encoding

Task-level ADK configuration goes in `task.metadata.adk`:

```yaml
- answer:
    wait:
      seconds: 0
    metadata:
      adk:
        agent:
          model: gemini-2.5-flash
          instruction: Answer using the supplied context.
          tools: []
          output_key: answer
          request_input:
            question: Approve this action?
```

Project-level registries and defaults go in `document.metadata.adk`:

```yaml
document:
  dsl: '1.0.3'
  namespace: examples
  name: draft-review
  version: '1.0.0'
  metadata:
    adk:
      agent_defaults:
        model: gemini-2.5-flash
        instruction: Default agent instruction.
      models:
        flash:
          model: gemini-2.5-flash
          provider:
            use: gemini
      providers:
        gemini:
          type: gemini
      memories:
        session:
          type: in-memory
do:
  - draft:
      wait:
        seconds: 0
      metadata:
        adk:
          agent:
            model:
              use: flash
            instruction: Draft a concise response.
            output_key: draft
```

Omitting `metadata.adk.agent` produces a deterministic ADK node. When the
extension is enabled, the translator creates an `LlmAgent` with `mode:
single_turn` and persists its result under `output_key` (default: task name).

Configuration is resolved in this order, with later entries overriding earlier
ones:

1. `document.metadata.adk.agent_defaults`
2. task-level ADK values (`task.metadata.adk.agent`)
3. `WORKFLOW_` environment variables

## Validation

The loader applies two-stage validation:

1. Validate the ADK payload with Pydantic (`TaskAdkMetadata` inside
   `task.metadata.adk`, `DocumentAdkMetadata` inside `document.metadata.adk`, plus
   `AgentCharacteristics`, `ModelSpec`, `ProviderConfig`, `MemoryConfig`).
2. Validate the document against the vendored OpenWorkflow v1.0.3 JSON Schema.
   The `metadata.adk` subtree is left intact because the upstream schema permits
   additional properties under `task.metadata` and `document.metadata`.

This keeps upstream validation strict while allowing ADK extensions to coexist
in the same document.

## `self_heal`

Task-level self-healing configuration also lives in `task.metadata.adk.self_heal`:

```yaml
metadata:
  adk:
    self_heal:
      max_attempts: 3
```

On a failing `try` task, error handling applies in this order:

1. the spec `catch.errors.with` filter (non-matching errors propagate),
2. the spec `catch.retry` policy (inline or `use.retries` reference),
3. the ADK `self_heal` extension (its diagnosis may patch state and retry),
4. the `catch.do` fallback tasks, capturing the error as `catch.as` if set.

The self-healer receives the unwrapped error (ADK's `DynamicNodeFailError`
wrapper is stripped), plus the current workflow state as context.



## Registries

Named models, providers, and memories are resolved centrally. A model may use
`provider: {use: name}` and an agent may use `memory: {use: name}`. Both can be
overridden with `WORKFLOW_PROVIDERS__...` and `WORKFLOW_MEMORIES__...`
environment variables.

## Sub-agents and teams

An agent may declare `sub_agents`, each with its own agent characteristics and
optional `name`. The translator builds an ADK coordinator tree; team members use
ADK's chat-mode `transfer_to_agent` mechanism. Sub-agent outputs do not become
workflow state keys unless an explicit `output_key` is configured.

## Human input

An agent may declare `request_input` to expose a `request_input` tool. The first
call raises a durable `human_input` suspension; resume the same run with
`run_workflow(..., resume=True, resume_input=...)`. The supplied value is
returned to the agent tool call, allowing approval or other external input to
be handled without replaying earlier workflow tasks.
