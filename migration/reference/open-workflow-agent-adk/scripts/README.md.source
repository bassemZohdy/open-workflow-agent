# Scripts

Small helper scripts used by CI and local development.

- `check_mutation_score.py` — Parses a `mutmut run` log and exits non-zero if the
  mutation score is below 80%. Used by the CI mutation-testing job.
- `fetch_schema.py` — Downloads the vendored OpenWorkflow schema baseline
  (`workflow.yaml` and `workflow.json`) for the version declared in
  `AGENTS.md`/`CLAUDE.md` into `src/openworkflow_adk/schema/vendor/<VERSION>/`.
  Run manually when the upstream spec baseline changes; not currently wired into
  CI.
