# Documentation guide

Use [Project Definition](<../Project Definition.md>) for the authoritative product
and architecture contract, [PROJECT.md](../PROJECT.md) for current verified status,
and [TODO.md](../TODO.md) for the ordered backlog. Source authoring and partial
passes do not establish delivery or conformance.

| Topic | Guide |
| --- | --- |
| Setup and deployment settings | [Configuration](configuration.md), [embedded defaults](embedded-bootstrap.md) |
| Public workflow contract | [Admission](workflow-admission.md), [expressions](workflow-expressions.md), [data flow](workflow-data-flow.md), [control flow](workflow-control-flow.md) |
| Native execution and persistence | [Task execution](native-task-execution.md), [checkpoint recovery](checkpoint-recovery.md) |
| Verified child and lifecycle profiles | [S2-07 acceptance](s2-07-child-workflow-acceptance.md), [S2-08 acceptance](s2-08-lifecycle-acceptance.md) |
| Unfinished continuation and protocol work | [S2-09 policy](s2-09-version-recovery-acceptance.md), [MCP gates](s3-01-mcp-acceptance.md), [A2A gates](s3-02-a2a-acceptance.md) |
| Unfinished operation and executable work | [S3-03 gates](s3-03-operation-adapters-acceptance.md), [S3-04 gates](s3-04-executable-source-acceptance.md) |
| Unfinished protected approvals | [S3-05 gates](s3-05-protected-approval-acceptance.md) |
| Unfinished identity and security | [S3-06 scope and gates](s3-06-identity-security-acceptance.md) |
| Sandbox boundary and deployment acceptance | [Sandbox execution](sandbox-execution.md), [verified cluster record](../PROJECT.md#verified-kubernetes-and-openshift-sandbox-acceptance--2026-09-12) |
| Inventory and consolidation | [Implementation audit](current-implementation-audit.md), [consolidation](agent-runtime-consolidation.md), [full feature tracker](../resources/compatibility/feature-tracker.json) |
| Historical validation | [Implementation evidence](validation-history/project-implementation-evidence.md), [backlog notes](validation-history/backlog-task-notes.md), [earlier delivery checkpoints](validation-history/backlog-delivery-2026-10-13.md), [unfinished batch results](validation-history/pending-batch-results.md) |

The historical records preserve their own source revisions, failures, passing
checks and exclusions. Read their dates alongside the current project summary;
an earlier successful run does not qualify a later source batch. The public OWS
schema, accepted architecture decisions and existing deployment gates remain
authoritative.
