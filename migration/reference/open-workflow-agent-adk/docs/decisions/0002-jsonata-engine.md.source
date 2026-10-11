# ADR 0002: JSONata engine

- Status: Accepted
- Date: 2026-08-10

## Decision

Use the pure-Python `jsonata-python` package as the expression engine. The
loader wrapper normalizes OpenWorkflow `${...}` delimiters, maps the
OpenWorkflow current-input prefix `.` to JSONata `$`, and adapts the common
`==` spelling used in workflow examples to JSONata equality.

## Consequences

Expression evaluation remains JSONata-compatible instead of growing a partial
interpreter in this project. Errors are surfaced as `ExpressionError`, while
mapping helpers provide the workflow-specific state update semantics.
