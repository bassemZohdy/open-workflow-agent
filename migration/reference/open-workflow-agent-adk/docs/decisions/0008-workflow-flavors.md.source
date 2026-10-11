:warning: **Superseded.** The separate catalog mode was removed; catalogs are
now resolved as standard qualified function calls while extended mode remains
the sole authoring flavor. This ADR is kept as historical context.

# ADR 0008: Extended workflow flavor

## Status

Superseded — catalog mode removed, extended mode remains the sole flavor.

## Historical decision

The project originally supported two intentionally separate authoring flavors:

- **Extended mode** allows ADK agent configuration through
  `task.metadata.adk.agent` and compiles agent tasks directly to Google ADK
  agents.
- **Catalog mode** kept workflow documents spec-pure and resolved named
  functions from a reusable external catalog file referenced by
  `use.catalogs.<name>.functions`.

Catalog mode was removed to simplify the project around a single extended
flavor. The `metadata.adk` encoding continues to be valid OpenWorkflow v1.0.3:
other implementors can parse the same documents and ignore the ADK metadata.

## Consequences

- Only extended mode is documented, tested, and exposed in the CLI/API.
- `use.catalogs` remains a standard OpenWorkflow registry and qualified calls
  are resolved before translation; it does not create a separate authoring
  mode.
- Shared reusable functions should be placed in `use.functions` or exposed as
  agent tools.
