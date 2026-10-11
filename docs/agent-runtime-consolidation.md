# One canonical workflow repository

Accepted on 2026-10-11: maintain `open-workflow-agent` as the main repository, consolidate useful predecessor material, and implement gaps in stages. This replaces the earlier preparation-only scope; remaining runtime features are not claimed complete.

| Source | Canonical disposition | Remaining boundary |
|---|---|---|
| open-workflow-agent-adk | All 284 tracked files preserved as frozen Apache references; selected fixtures adapted to canonical contract tests | Native graph/provider/broker/memory ports and old import/CLI compatibility |
| generic-agent-lang | All 102 file hashes and refs indexed; eight pattern families tracked; independent assistant/pipeline OWS recipes | No detected license grant; source stays at origin; unsafe approval excluded |
| specs-agent-platform | All 109 pinned tracked files imported as Apache references after explicit transfer authorization | Native compiler integration and semantic/checkpoint corrections remain staged work |

Pinned public revisions, refs and every file disposition are in [the source manifest](../migration/source-manifest.json). The [reference guide](../migration/README.md) explains provenance. Frozen code is not installed, packaged or imported as a second engine. Source repositories have not yet been archived. Keep their histories, releases, branches and useful open PRs; do not delete them.

The imported ADK expression engines, direct execution paths, custom checkpoint/history services and legacy package identity require reconciliation before production use. Adapted fixtures identify changes and retain Apache attribution. Canonical tests prove only the selected fixture behavior, not full predecessor API compatibility.

## Implementation rules

- Reuse native LangGraph graph nodes, tasks, routing and checkpoints when they preserve OWS behavior. The current Functional API envelope still executes the common interpreter; native task-level lowering remains a gap.
- Start in one container with embedded storage. External databases, event transports and providers are optional bootstrap bindings.
- Put task/workflow behavior in workflow YAML/JSON; put environment preparation, installed providers, storage endpoints, credentials and defaults in separate bootstrap configuration.
- Keep the official schema unchanged. Versioned custom metadata extensions may declare native capabilities; reject unsupported required features.
- Reuse common identity, errors, approvals, operation records and SandboxManager. Native checkpoint state stays engine-private.
- Verify Python and JS independently. Native API existence is not outcome equivalence.

## Gaps and legacy behavior

[TODO.md](../TODO.md) is the short staged backlog. The [full tracker](../resources/compatibility/feature-tracker.json) retains 658 feature rows, 443 proposed tests, 774 schema constraints, 83 extension decisions and 52 configuration entries. Every feature maps to TODO tasks. Proposed tests are not marked executed by import.

Main gaps: full jq, data/export/context rules, scoped termination, ordered fork outputs, native task compilation, durable race cancellation, persistent events/timers and native/common-state recovery. Protocol coverage, advanced native behavior and JS parity follow later.

All eight agent families are preserved in [the pattern catalog](../resources/patterns/pattern-catalog.json). Per-role models/tools, budgets, route failures, response shapes and resume/cancellation need tests before old API equivalence can be claimed.

The generic approval prototype can default missing status to approved and route unknown states to completion. It must not be activated. Use the protected durable approval service and prove operator binding, denial/expiry, restart/replay and zero side effects before approval.

Standalone Apache `open-workflow-agent-adk` v0.2.1 imports `openworkflow_adk`; the canonical MIT distribution v0.2.0 imports `open_workflow_agent_adk`. Archiving does not rename packages or migrate installed consumers. Decide compatibility packaging or documented API retirement before replacement releases. Retain open PRs and old releases; do not merge unrelated dependency changes solely for archival.

`open-simple-agent`, `generic-agent-adk` and other repositories are outside the named three-source archive instruction and remain separate.

## Archive and rollback

Archive after reviewed material/disposition is published and preservation checks pass. GitHub archival is reversible and retains source history. It freezes maintenance; it does not assert that all legacy features are implemented here. Existing pinned releases remain available; unarchive if old-boundary maintenance is needed.

Private-source publication was authorized after the explicit visibility question and is completed in this consolidation. The source remains private at origin. The GitHub connector has no archive action, and the permitted browser fallback is not authenticated; archival currently needs the user to perform the action. Never report archival complete until repository metadata confirms it.
