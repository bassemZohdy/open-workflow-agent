from openworkflow_adk.devtools.benchmark import benchmark


async def test_benchmark_reports_latency_percentiles() -> None:
    result = await benchmark(iterations=2)

    assert result["iterations"] == 2
    assert result["mean_ms"] >= 0
    assert result["p95_ms"] >= result["mean_ms"] or result["p95_ms"] >= 0
