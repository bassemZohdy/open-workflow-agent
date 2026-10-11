# Upstream schema sync runbook

1. Run `uv run spec-drift-check` and record the upstream schema version.
2. Fetch a candidate schema with `python scripts/fetch_schema.py --version <version>`.
3. Review changes in a separate branch; update typed models and fixtures before changing the
   default DSL version.
4. Run the complete test suite, schema validation tests, and the fixture parser.
5. Update `docs/reference/task-coverage.md`, `CHANGELOG.md`, and the compatibility matrix.

The vendored `1.0.3` schema remains the default until an explicit migration is reviewed.
Patch releases on the same `1.0.x` line use the baseline through the compatibility
shim; minor or major versions require a new vendored schema and translator review.
