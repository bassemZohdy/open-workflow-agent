# ADR 0003: Implement `fork.compete` as a first-success race

## Decision

Translate `fork.compete` branches into asyncio tasks and return the first
successful branch result. Cancel unfinished branches after the winner completes;
if every branch fails, surface the final failure.

## Rationale

The construct is a race, not a fan-out aggregation. Running branches concurrently
preserves that semantic while avoiding a dependency on a specific ADK parallel-node
implementation.

## Consequences

Branch side effects may occur before cancellation. Workflows that require all
branches to finish should use ordinary parallel composition instead.
