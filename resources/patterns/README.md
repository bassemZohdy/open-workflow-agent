# Portable agent patterns

All eight requested families have independently authored Open Workflow 1.0.3
recipes. They are staged source, awaiting the shared engine, process recovery,
platform and actual PostgreSQL validation gates in
[the S3-07 acceptance record](../../docs/s3-07-agent-pattern-acceptance.md).
No predecessor source was copied and no legacy Python API compatibility is claimed.

| Family | Recipe | Finite behavior |
| --- | --- | --- |
| Assistant | [assistant.yaml](assistant.yaml), [named role](assistant-roles.yaml) | One response |
| Pipeline | [pipeline.yaml](pipeline.yaml), [named roles](pipeline-roles.yaml) | Draft followed by review |
| Multi perspective | [multi-perspective.yaml](multi-perspective.yaml) | Two isolated branches, ordered synthesis |
| Refine until good | [refine-until-good.yaml](refine-until-good.yaml) | At most three draft/evaluation attempts; exhaustion faults |
| Expert dispatch | [expert-dispatch.yaml](expert-dispatch.yaml) | Validated research/finance route; unknown routes fault |
| Team coordinator | [team-coordinator.yaml](team-coordinator.yaml) | Two fixed awaited child workflows; ordered synthesis |
| Plan and execute | [plan-and-execute.yaml](plan-and-execute.yaml) | One to three validated research/finance actions |
| Approval gate | [approval-gate.yaml](approval-gate.yaml) | Protected durable approval before the action role |

The original assistant/pipeline 1.0.0 recipes and task references stay unchanged.
Their separate role variants and the other recipes require deployment configuration
for exactly the catalog role references they call. Prompts or workflow metadata do
not configure providers, grant tool access or carry credentials. The coordinator
also requires [researcher-worker.yaml](researcher-worker.yaml) and
[finance-worker.yaml](finance-worker.yaml) in the deployment workflow catalog.

Most recipes accept `{question: string}`. The approval recipe additionally needs
`requestId`, RFC3339 `expiresAt`, and the original named `assignee`; optional
`reassignmentTargets` and `escalationTargets` use the protected approval service.
Unapproved decisions end without invoking the action role. Pending requests wait
with a bounded timeout; expired, denied, malformed and unknown decisions never
select the approved branch. Deployment must enable protected approvals and bind
actual named actor security profiles before using this recipe.

These finite recipes do not permit dynamically selected workflow names, recursive
agent delegation, model-selected providers or unconstrained actions. Agent roles
cannot launch child workflows; fixed OWS child calls use the existing deployment
child budgets. Required native behaviors outside this profile remain subject to
explicit capability admission and the S4 native extension work.
