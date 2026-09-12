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
  bzohdy/open-workflow-agent-adk:0.2.0
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

Deployment configuration recipes:

| Example | Demonstrates |
| --- | --- |
| [`a2a-agent.yaml`](a2a-agent.yaml) | A2A Agent Card exposure, named bearer authentication, and deployment-declared skills. |
| [`security-profiles.yaml`](security-profiles.yaml) | Bearer, API-key, OAuth2 client-credentials, and mTLS profile shapes. |
| [`traffic-policy.yaml`](traffic-policy.yaml) | Token-bucket rate limiting and concurrent-request admission. |
| [`sandbox-docker.yaml`](sandbox-docker.yaml) | Digest-pinned Docker sandbox policy through the restricted controller boundary. |
| [`sandbox-kubernetes.yaml`](sandbox-kubernetes.yaml) | Digest-pinned Kubernetes/OpenShift sandbox policy with enforced network/process controls. |
| [`protocol-tools.yaml`](protocol-tools.yaml) | Deployment-controlled MCP, OpenAPI, and A2A tool endpoints. |
| [`postgres.yaml`](postgres.yaml) | PostgreSQL common persistence configuration and secret-injection guidance. |

## Notes

- The deterministic `fake/default` model requires no API key. Swap `model.provider` to `litellm` with a real model name for production (see [configuration](../docs/configuration.md#model)).
- External catalogs and sandbox execution are disabled by default and fail closed without explicit deployment configuration.
- Scheduling has no cron support in the bounded profile; use `schedule.after`/`schedule.every`.
- The deployment recipes contain placeholders for endpoints, image digests, and secret environment variables; replace them before use.
