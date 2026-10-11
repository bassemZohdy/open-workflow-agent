# Workflow flavors

OpenWorkflow ADK focuses on a single authoring flavor: **extended mode**.

Extended workflows use `task.metadata.adk.agent` to invoke a Google ADK agent
inline. Reusable models, providers, and memories are registered under
`document.metadata.adk`:

```yaml
document:
  dsl: '1.0.3'
  namespace: examples
  name: hello-agent
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
  - greet:
      wait:
        seconds: 0
      metadata:
        adk:
          agent:
            model:
              use: flash
            instruction: Greet the user by name.
            output_key: greeting
```

This encoding is valid OpenWorkflow v1.0.3: other implementors can parse the
same file and ignore the `metadata.adk` block.

Use extended mode when you need ADK agents, tools, memory, agent teams, or
inline instruction authoring.

## Mode selection

The CLI and API accept `--mode auto|extended`. `auto` is the default and simply
selects extended mode when an ADK agent is present (`task.metadata.adk.agent`).
Pass `--mode extended` to enforce a hard policy.

## Reusable functions

Workflow-level `use.functions` remains available in OpenWorkflow v1.0.3 for
in-document reusable functions. Qualified catalog calls such as
`validateEmail:1.0.0@shared` are also supported when `use.catalogs.shared`
defines a catalog root. The runtime resolves
`functions/validateEmail/1.0.0/function.yaml` before building the graph.

See [ADR 0008](decisions/0008-workflow-flavors.md) and the
[extended reference](reference/extended.md).
