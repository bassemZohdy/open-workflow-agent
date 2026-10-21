# S4-08: predecessor import, CLI, and package compatibility

## Decision

The canonical adapter does not promise source, command-line, or install-time
compatibility with the archived `open-workflow-agent-adk` predecessor. Do not
add an `openworkflow_adk` import alias, restore the predecessor `owf-adk` or
`spec-drift-check` entry points as compatibility shims, or claim that the
canonical adapter is a drop-in replacement. The predecessor's releases and
source remain available for consumers that need its API.

Any useful developer tooling selected for the canonical runtime must be
implemented and documented as canonical tooling, with its own supported
commands and contracts. It must not import or execute frozen predecessor
modules. That tooling work remains open under S4-08; this decision alone does
not complete the task.

The canonical root package now authors an `owa validate PATH` command. It loads
and compiles a workflow against the default OWA portable profile and reports a
safe common error without exposing the internal execution plan. It does not
resolve deployment credentials or trusted external catalogs and does not apply
engine-specific capability or runtime configuration. A workflow that depends
on those settings still needs validation in its target deployment. The command
and its focused tests are authored but have not yet been run; this does not
complete S4-08. The [developer guide](development.md#validate-a-workflow-locally)
documents source-checkout installation and use, its default-profile limits, and
the fact that validation performs no model invocation or credential resolution.

Before publishing a future Python distribution, resolve the distribution-name
collision explicitly. Both package metadata records use the normalized
distribution name `open-workflow-agent-adk`, while their import roots and
contents differ. The archived metadata is version 0.2.1 and the canonical
adapter metadata is version 0.2.0. They cannot be treated as two independently
installable distributions under that same name; choose and document the
canonical release identity and migration path before publishing or replacing
the predecessor package. This repository decision does not itself transfer
ownership of an external package index name or remove existing releases.

## Evidence and limits

The migration manifest pins the predecessor to
[`open-workflow-agent-adk` revision
`48db0cf5e1a1d3202cbbec6021736e1d89e94cd0`](../migration/source-manifest.json)
and preserves its 284 tracked files as frozen Apache references. In that
snapshot, `pyproject.toml` declares distribution `open-workflow-agent-adk`
version `0.2.1`, Python `>=3.10`, import package `openworkflow_adk`, and the
`owf-adk` and `spec-drift-check` console scripts. Its README demonstrates
`from openworkflow_adk import load, run_workflow` and the `owf-adk` commands.
The predecessor package root says only names in `__all__` are stable; other
imports are provisional and may move before v1.0. Its changelog marks several
developer and interop surfaces provisional.

The canonical [`engines/adk/pyproject.toml`](../engines/adk/pyproject.toml)
declares the same distribution name at version `0.2.0`, Python `>=3.12`, and
wheel package `open_workflow_agent_adk`. It declares neither predecessor
console script. The canonical adapter is MIT-licensed and depends on the
framework-neutral core. These package and contract differences support the
no-shim decision; they do not establish whether any particular downstream
consumer still requires the predecessor API.

No consumer inventory or package-index ownership/transfer record is present in
the repository. The manifest preserves a source snapshot and selected refs,
including tags through `v0.2.0`; it does not preserve the complete Git history
or a separately verified `0.2.1` release artifact. The `0.2.1` evidence here is
the frozen snapshot's package metadata and changelog. Those external release
and consumer facts remain unresolved. Existing predecessor releases should be
retained, and any release-name or consumer-migration action requires separate
evidence and ownership decisions. Frozen Apache files keep their provenance
and are not active compatibility code.
