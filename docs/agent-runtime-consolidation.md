# One canonical workflow repository

Accepted on 2026-10-11: maintain `open-workflow-agent` as the main repository, consolidate useful predecessor material, and implement gaps in stages. This replaces the earlier preparation-only scope; remaining runtime features are not claimed complete.

| Source | Canonical disposition | Remaining boundary |
|---|---|---|
| open-workflow-agent-adk | All 284 tracked files preserved as frozen Apache references; selected fixtures adapted to canonical contract tests | Native graph/provider/broker/memory ports and old import/CLI compatibility |
| generic-agent-lang | All 102 file hashes and refs indexed; eight pattern families tracked; independent assistant/pipeline OWS recipes | No detected license grant; source stays at origin; unsafe approval excluded |
| specs-agent-platform | All 109 pinned tracked files imported as Apache references after explicit transfer authorization | Native compiler integration and semantic/checkpoint corrections remain staged work |

Pinned public revisions, refs and every file disposition are in [the source manifest](../migration/source-manifest.json). The [reference guide](../migration/README.md) explains provenance. Frozen code is not installed, packaged or imported as a second engine. GitHub metadata confirms all three named source repositories are archived. Their histories, releases, branches and useful open PRs remain preserved.

The imported ADK expression engines, direct execution paths, custom checkpoint/history services and legacy package identity require reconciliation before production use. Adapted fixtures identify changes and retain Apache attribution. Canonical tests prove only the selected fixture behavior, not full predecessor API compatibility.

## Implementation rules

- Reuse native LangGraph graph nodes, tasks, routing and checkpoints when they preserve OWS behavior. The LangGraph adapter now lowers admitted scopes to native task nodes and conditional routing; see [the execution/recovery boundary](native-task-execution.md).
- Start in one container with embedded storage. External databases, event transports and providers are optional bootstrap bindings.
- Put task/workflow behavior in workflow YAML/JSON; put environment preparation, installed providers, storage endpoints, credentials and defaults in separate bootstrap configuration.
- Keep the official schema unchanged. Versioned custom metadata extensions may declare native capabilities; reject unsupported required features.
- Reuse common identity, errors, approvals, operation records and SandboxManager. Native checkpoint state stays engine-private.
- Verify Python and JS independently. Native API existence is not outcome equivalence.

## Gaps and legacy behavior

[TODO.md](../TODO.md) is the short staged backlog. The [full tracker](../resources/compatibility/feature-tracker.json) retains 658 feature rows, 443 proposed tests, 774 schema constraints, 83 extension decisions and 52 configuration entries. Every feature maps to TODO tasks. Proposed tests are not marked executed by import.

Verified Stage 1 work records jq, data/export/context rules and scoped termination in PROJECT.md. Native LangGraph task compilation is also verified against the same shared fixtures, private checkpoints and all required acceptance gates. Finite Stage 2 acceptance now covers ordered fork outcomes, durable race cancellation, events, timers, child calls and lifecycle controls at the revisions linked from PROJECT.md. This does not close every related inventory row. Original-definition continuation and the later protocol, operation, executable-source, protected-approval and identity batches still await their own complete qualification. Advanced native behavior and independent JS parity remain later stages.

All eight agent families are preserved in [the pattern catalog](../resources/patterns/pattern-catalog.json). Per-role models/tools, budgets, route failures, response shapes and resume/cancellation need tests before old API equivalence can be claimed.

The generic approval prototype can default missing status to approved and route unknown states to completion. It must not be activated. Use the protected durable approval service and prove operator binding, denial/expiry, restart/replay and zero side effects before approval.

Standalone Apache `open-workflow-agent-adk` v0.2.1 imports `openworkflow_adk`; the canonical MIT distribution v0.2.0 imports `open_workflow_agent_adk`. Archiving does not rename packages or migrate installed consumers. Decide compatibility packaging or documented API retirement before replacement releases. Retain open PRs and old releases; do not merge unrelated dependency changes solely for archival.

`open-simple-agent`, `generic-agent-adk` and other repositories are outside the named three-source archive instruction and remain separate.

## Archive and rollback

Archive after reviewed material/disposition is published and preservation checks pass. GitHub archival is reversible and retains source history. It freezes maintenance; it does not assert that all legacy features are implemented here. Existing pinned releases remain available; unarchive if old-boundary maintenance is needed.

Private-source publication was authorized after the explicit visibility question
and is completed in this consolidation. On 2026-10-11, repository metadata
confirmed `isArchived=true` for
[open-workflow-agent-adk](https://github.com/bassemZohdy/open-workflow-agent-adk),
[generic-agent-lang](https://github.com/bassemZohdy/generic-agent-lang) and
[specs-agent-platform](https://github.com/bassemZohdy/specs-agent-platform).
The first two remain public and the specs origin remains private. The source
manifest records the verification time and metadata; the two preservation
checks still verify exact Apache file bytes and the unlicensed-source boundary.
No new archive action or visibility change was needed. MIG-02 is verified;
archival does not claim that the remaining staged runtime work is complete.
