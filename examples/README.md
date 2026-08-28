# Examples

Standalone configuration and workflow examples for the Open Workflow Agent. They are plain files — no source checkout is required to use them with the published images.

## How to use them

The published images read runtime configuration from `/config/agent.yaml` and workflow definitions referenced by it. Mount the example files instead of your own:

```bash
mkdir -p owa/config owa/knowledge owa/data
cp examples/agent.yaml owa/config/agent.yaml
cp examples/workflow.yaml owa/config/workflow.yaml
docker run --rm --name open-workflow-agent \
  -p 8080:8080 \
  -v "$(pwd)/owa/config:/config:ro" \
  -v "$(pwd)/owa/knowledge:/knowledge:ro" \
  -v "$(pwd)/owa/data:/data" \
  bzohdy/open-workflow-agent-adk:0.1.0
```

The same layout works for the LangGraph image, Kubernetes ConfigMaps/volumes, and OpenShift. See [getting-started](../docs/getting-started.md) and [deployment](../docs/deployment.md).

## Product-goal ladder

| Example | Demonstrates |
| --- | --- |
| [`agent.yaml`](agent.yaml) + [`workflow.yaml`](workflow.yaml) | Minimal agent invocation with a knowledge-backed workflow (`agent:1.0.0@default`, `search_knowledge`). |
| [`memory-agent.yaml`](memory-agent.yaml) | Long-term memory tools (`add_memory`/`search_memory`/`delete_memory`) with `memory.enabled: auto`. |
| [`tools-agent.yaml`](tools-agent.yaml) | A configured agent tool (bounded protocol clients, deployment-controlled). |
| [`scheduled-workflow.yaml`](scheduled-workflow.yaml) | Workflow-side `schedule.every` (durable, at-least-once, single-runtime ownership). |
| [`approval-workflow.yaml`](approval-workflow.yaml) | Durable human-in-the-loop approval composed from `emit` + `listen` (requires `approvals.enabled`). |
| [`subworkflow-main.yaml`](subworkflow-main.yaml) | Local sub-workflows: `workflow.catalog` registration + `run.workflow`. |
| [`external-catalog.yaml`](external-catalog.yaml) | External catalog authoring shape (`use.catalogs`); requires a deployment trust policy, otherwise rejected. |

## Notes

- The deterministic `fake/default` model requires no API key. Swap `model.provider` to `litellm` with a real model name for production (see [configuration](../docs/configuration.md#model)).
- External catalogs and sandbox execution are disabled by default and fail closed without explicit deployment configuration.
- Scheduling has no cron support in the bounded profile; use `schedule.after`/`schedule.every`.
