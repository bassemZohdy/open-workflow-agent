"""Smoke-test the dependency-free runtime benchmark harness."""

from __future__ import annotations

import pytest

from benchmarks.runtime import run_benchmark


@pytest.mark.asyncio
async def test_runtime_benchmark_reports_pipeline_metrics() -> None:
    result = await run_benchmark(iterations=2, concurrency=2)

    assert result["iterations"] == 2
    assert result["concurrency"] == 2
    assert result["compilation_seconds_total"] >= 0
    assert result["sequential_seconds_per_invocation"] >= 0
    assert result["concurrent_throughput_per_second"] > 0
