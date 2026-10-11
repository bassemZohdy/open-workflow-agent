# ADR 0004: Implement `try`/`catch` around nested task execution

## Decision

Translate `try` as a nested task sequence and catch exceptions at that boundary.
The matching catch entry is selected by its configured error type (or the
wildcard), and its `do` sequence receives the error context in workflow state.

## Rationale

Handling errors at the nested-sequence boundary keeps normal task behavior
unchanged and gives catch handlers the same state and expression facilities as
ordinary tasks.

## Consequences

Errors without a matching catch entry continue to the ADK runner. Catch handlers
can recover by completing normally or re-raise a new workflow error explicitly.
