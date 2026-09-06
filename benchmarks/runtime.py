"""Benchmark the common workflow compilation and invocation pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from tempfile import TemporaryDirectory
from typing import Any

from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowPlan, compile_workflow

BENCHMARK_WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "benchmarks",
        "name": "runtime-pipeline",
        "version": "1.0.0",
    },
    "do": [
        {
            "prepare": {
                "set": {
                    "answer": "${ .input }",
                }
            }
        }
    ],
}


def benchmark_compilation(iterations: int) -> float:
    """Return total seconds spent compiling ``iterations`` fresh plans."""

    started = time.perf_counter()
    for _ in range(iterations):
        compile_workflow(BENCHMARK_WORKFLOW)
    return time.perf_counter() - started


def _new_handle(services: RuntimeServices, plan: WorkflowPlan):
    return services.invocations.create(
        engine="portable",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


async def run_benchmark(*, iterations: int = 50, concurrency: int = 10) -> dict[str, Any]:
    """Run compilation, sequential latency, and concurrent throughput probes."""

    if iterations < 1 or concurrency < 1:
        raise ValueError("iterations and concurrency must be positive")

    plan = compile_workflow(BENCHMARK_WORKFLOW)
    compilation_seconds = benchmark_compilation(iterations)
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    with TemporaryDirectory(prefix="owa-benchmark-") as database_root:
        services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
        engine = PortableWorkflowEngine()
        await engine.initialize(services)

        async def invoke_once() -> Any:
            return await engine.invoke(plan, _new_handle(services, plan), {"value": "ok"})

        try:
            sequential_started = time.perf_counter()
            sequential_results = [await invoke_once() for _ in range(iterations)]
            sequential_seconds = time.perf_counter() - sequential_started

            concurrent_started = time.perf_counter()
            concurrent_results = await asyncio.gather(*(invoke_once() for _ in range(concurrency)))
            concurrent_seconds = time.perf_counter() - concurrent_started
        finally:
            await engine.shutdown()
            services.close()

    results = [*sequential_results, *concurrent_results]
    if any(result.status != "completed" for result in results):
        raise RuntimeError("benchmark invocation did not complete successfully")
    return {
        "iterations": iterations,
        "concurrency": concurrency,
        "compilation_seconds_total": compilation_seconds,
        "compilation_seconds_per_iteration": compilation_seconds / iterations,
        "sequential_seconds_total": sequential_seconds,
        "sequential_seconds_per_invocation": sequential_seconds / iterations,
        "concurrent_seconds_total": concurrent_seconds,
        "concurrent_throughput_per_second": concurrency / max(concurrent_seconds, 1e-12),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    result = asyncio.run(run_benchmark(iterations=args.iterations, concurrency=args.concurrency))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
