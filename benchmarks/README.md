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
