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
modules. The selected canonical tool is `owa validate PATH`, described below.
The repository delivery and migration decision is now explicit in
[the distribution policy](../resources/python-distribution-policy.json).
Task completion still requires the current-source acceptance gates.

The canonical root package now provides an `owa validate PATH` command. It loads
and compiles a workflow against the default OWA portable profile and reports a
safe common error without exposing the internal execution plan. It does not
resolve deployment credentials or trusted external catalogs and does not apply
engine-specific capability or runtime configuration. A workflow that depends
on those settings still needs validation in its target deployment. Its focused
tests passed locally in the corrected consolidated batch. A real official-schema
and compiler check also verifies that workflow data and the private plan are
not printed. The [developer guide](development.md#validate-a-workflow-locally)
documents source-checkout installation and use, its default-profile limits, and
the fact that validation performs no model invocation or credential resolution.

The canonical source distribution retains `open-workflow-agent-adk` and the
`open_workflow_agent_adk` import. Supported delivery is a pinned source checkout
or a qualified OCI image. Python package-index publication is disabled; the
release workflow publishes OCI images and GitHub release notes. CI checks the
real metadata, absence of predecessor shims, canonical CLI entry point and
absence of an index publisher. The existing canonical package/API names stay
unchanged, and no automatic predecessor replacement is offered.

Both metadata records use the normalized distribution name
`open-workflow-agent-adk`, with different import roots and contents. The archived
metadata is version 0.2.1 and the canonical metadata is version 0.2.0. Install
them in separate environments; they cannot coexist as independent distributions
under that name. This version ordering promises no upgrade path between them.
A future index publisher needs a verified owner/transfer record, a release-version
decision and consumer migration evidence before its policy can be activated.
Existing predecessor releases remain available.

## Consumer migration

1. Retain the predecessor environment, pinned dependency set and saved workflows
   needed to reproduce its outcomes.
2. Create a separate canonical checkout/environment or use a qualified canonical
   image. Use its own locked ADK environment from the developer guide; do not
   install it over a predecessor environment.
3. Port workflow resources to the canonical OWS 1.0.3, model, tool, role and
   deployment-security contracts. Replace predecessor host API calls explicitly;
   no import or CLI alias performs this translation.
4. Run `owa validate PATH`, then exercise the workflow on the selected native
   engine and persistence backend with deterministic fixtures. Confirm outputs,
   rejection behavior and saved-invocation recovery against the intended profile.
5. Move a consumer only after its outcome checks pass. Keep the original
   environment for rollback; predecessor checkpoints are not canonical checkpoints.

This is an opt-in manual migration policy. This repository change performs no
consumer discovery, automatic rewrite, package transfer or deployment.

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
and consumer facts remain unresolved and gate any future index publication or
managed migration service. They do not change the chosen no-shim, isolated
environment policy. Frozen Apache files keep their provenance and are not active
compatibility code.
