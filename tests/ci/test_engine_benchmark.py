"""Smoke-test the adapter benchmark without requiring native SDK extras."""

from __future__ import annotations

import pytest

from benchmarks.engines import run_engine_benchmark


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name", ["adk", "langgraph"])
async def test_engine_benchmark_reports_resume_and_checkpoint_metrics(engine_name: str) -> None:
    result = await run_engine_benchmark(engine_name, iterations=1, concurrency=1)

    assert result["engine"] == engine_name
    assert result["engine_compile_seconds_per_iteration"] >= 0
    assert result["concurrent_throughput_per_second"] > 0
    assert result["native_checkpoint_bytes_delta"] >= 0
    assert result["interruption_resume"]["status"] == "completed"
    assert result["interruption_resume"]["side_effect_calls"] >= 2
    assert result["interruption_resume"]["unique_operation_ids"] == 1
