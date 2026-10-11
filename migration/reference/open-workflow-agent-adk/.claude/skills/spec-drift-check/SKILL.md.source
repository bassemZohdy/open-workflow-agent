---
name: spec-drift-check
description: Checks whether the OpenWorkflow Specification has released a newer version than the one this project's extension targets (currently v1.0.3), and summarizes what changed. Use when asked to check for spec updates, before extending the schema further, or periodically to catch upstream drift.
---

1. Fetch `https://open-workflow-specification.org/` and note the current published spec version.
2. Compare it against the baseline recorded in this repo's `CLAUDE.md` (currently v1.0.3).
3. If the upstream version is newer:
   - Fetch the changelog/release notes if linked from the spec site.
   - Fetch the new schema (`/schemas/<version>/workflow.yaml` or `.json`) and diff its structure against the version this project's translator/extension currently assumes.
   - Summarize concrete breaking or additive changes relevant to task definitions (`do`, `call`, `run`, `switch`, `fork`, `try`/`catch`, `wait`, `emit`/`listen`) and to the `document` block.
4. Report findings to the user: version delta, relevant schema changes, and whether the agent-characteristics extension in this project needs updating. Do not modify code or CLAUDE.md automatically — just report.
