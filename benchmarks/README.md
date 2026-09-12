# Runtime benchmarks

The dependency-free benchmark harness measures the common runtime pipeline:

- workflow compilation latency;
- sequential invocation latency;
- concurrent invocation throughput.

Run a small local probe with:

```bash
uv run python -m benchmarks.runtime --iterations 50 --concurrency 10
```

The output is JSON so CI or a later benchmark runner can archive and compare
results. These measurements use the deterministic fake model and are intended
for regression comparison on a stable runner, not as portable performance
claims across machines.

## Engine adapter probes

The engine-specific harness keeps adapter evidence separate from the common
runtime probe:

```bash
uv run --directory engines/adk --locked --extra native --extra knowledge --with pytest --with pytest-asyncio \
  python ../../benchmarks/engines.py --engine adk --iterations 10 --concurrency 4
uv run --directory engines/langgraph --locked --extra sqlite --extra knowledge --with pytest --with pytest-asyncio \
  python ../../benchmarks/engines.py --engine langgraph --iterations 10 --concurrency 4
```

It reports the adapter `compile` boundary, sequential latency, concurrent
throughput, native checkpoint-file growth, and an interruption/resume probe.
The probe deliberately uses a side-effecting HTTP task and records its
operation ids: replay is successful only when the same idempotency key is
reused. A zero native checkpoint delta means the selected adapter fell back to
the common executor or used in-memory native state; compare it only with runs
of the same adapter, dependency lock, and runner image.
