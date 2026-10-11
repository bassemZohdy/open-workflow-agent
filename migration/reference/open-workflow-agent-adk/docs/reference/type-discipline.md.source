# Type-discipline policy

The package is marked `py.typed`, but workflow documents are intentionally
dynamic: YAML values, expression results, ADK callbacks, and plugin boundaries
are represented as `Any` at the translation seam. Those uses are documented by
the models and task-builder interfaces rather than hidden behind unsafe casts.

Public APIs use typed models (`OpenWorkflowDocument`, `Task`, registries, and
configuration objects). New code should keep `Any` at adapter boundaries,
prefer concrete types internally, and avoid adding `type: ignore` without a
local comment explaining the third-party typing limitation.
