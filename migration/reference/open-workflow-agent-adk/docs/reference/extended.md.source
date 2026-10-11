# Extended mode reference

Extended mode is the ADK-native flavor of OpenWorkflow. It adds ADK-specific
configuration inside OpenWorkflow-compatible metadata containers so that the
same YAML file is still valid OpenWorkflow v1.0.3 for other implementors.

- Task-level ADK config lives in `task.metadata.adk`.
- Project-level registries live in `document.metadata.adk`.

## `metadata.adk.agent` task extension

An `metadata.adk.agent` block turns a task into an ADK LLM agent invocation:

```yaml
document:
  dsl: '1.0.3'
  namespace: examples
  name: draft-review
  version: '1.0.0'
  metadata:
    adk:
      models:
        flash:
          model: gemini-2.5-flash
          provider:
            use: gemini
      providers:
        gemini:
          type: gemini
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
  - review:
      wait:
        seconds: 0
      metadata:
        adk:
          agent:
            model:
              use: flash
            instruction: Review and improve {draft}.
            output_key: reviewed
```

An agent task can also include `wait` (e.g. `wait: {seconds: 0}`) so that the
task has a deterministic kind alongside the ADK extension.

## Registries

`document.metadata.adk` supports three registries that agents can reference by
name:

- `document.metadata.adk.models` — named `ModelSpec` objects pointing to a
  provider and model.
- `document.metadata.adk.providers` — provider configurations (e.g., `gemini`,
  `openai`).
- `document.metadata.adk.memories` — memory backends referenced by agent tasks.

Example:

```yaml
document:
  metadata:
    adk:
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
```



## Tools

Agents can declare tools by name under `metadata.adk.agent.tools`. The tools
must be registered through the translator's function registry or available as
built-ins.

```yaml
  - lookup:
      metadata:
        adk:
          agent:
            model:
              use: flash
            instruction: Look up the value.
            tools: [lookup]
```

## Multi-agent teams

An agent can spawn sub-agents through `metadata.adk.agent.sub_agents`, creating
a team where the parent agent can delegate work.

```yaml
  - lead:
      metadata:
        adk:
          agent:
            model:
              use: flash
            instruction: Coordinate the team.
            sub_agents:
              - model:
                  use: flash
                instruction: Research the topic.
                name: researcher
              - model:
                  use: flash
                instruction: Summarize findings.
                name: summarizer
```

## Self-heal

Task-level self-healing configuration lives in
`task.metadata.adk.self_heal`.

```yaml
  - risky:
      metadata:
        adk:
          self_heal:
            max_attempts: 3
```

## Interoperability guarantee

A document authored with the `metadata.adk` encoding is valid OpenWorkflow
v1.0.3. Other implementors can parse it and ignore the ADK block. The loader
validates the ADK payload separately and then validates the remainder of the
document against the vendored upstream schema, leaving `metadata.adk` intact.

Use `owf-adk lint --strict` (or `owf-adk export --format openworkflow`) to
emit a pure OpenWorkflow document with ADK metadata stripped.

## When to use extended mode

Use extended mode when you need:

- Inline ADK agent configuration.
- Tool calling, memory, or structured output from an LLM.
- Multi-agent teams.
- Tight integration with ADK runners and telemetry.

For portable, spec-pure workflows, keep shared logic in `use.functions` or
expose it through agent tools.
