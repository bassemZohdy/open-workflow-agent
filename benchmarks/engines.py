"""Measure engine-adapter behavior separately from the common runtime."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import httpx
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

ROOT = Path(__file__).resolve().parents[1]
for source_root in (ROOT / "engines/adk/src", ROOT / "engines/langgraph/src"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

ENGINE_MODULES = {
    "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
    "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
}

BENCHMARK_WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "benchmarks",
        "name": "engine-envelope",
        "version": "1.0.0",
    },
    "do": [
        {
            "answer": {
                "set": {"answer": "${ .input }"},
            }
        }
    ],
}

RESUME_WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "benchmarks",
        "name": "engine-resume",
        "version": "1.0.0",
    },
    "do": [
        {
            "side_effect": {
                "call": "http",
                "with": {
                    "method": "POST",
                    "endpoint": "https://benchmark.test/side-effect",
                    "body": {"operation": "replay-safe"},
                },
            }
        },
        {"pause": {"wait": {"milliseconds": 50}}},
        {"finish": {"set": {"done": True}}},
    ],
}


def _load_engine(engine_name: str) -> tuple[type[Any], bool]:
    try:
        module_name, class_name = ENGINE_MODULES[engine_name]
    except KeyError as exc:
        raise ValueError(f"unsupported engine: {engine_name}") from exc
    module = importlib.import_module(module_name)
    engine_type = getattr(module, class_name)
    native_adapter = getattr(engine_type(), "native", None)
    native = getattr(native_adapter, "available", None)
    if native is None:
        native = getattr(
            importlib.import_module(f"{module_name}.native"),
            f"{engine_name.upper()}_AVAILABLE",
            False,
        )
    return engine_type, bool(native)


def _handle(services: RuntimeServices, engine_name: str, plan: Any) -> Any:
    return services.invocations.create(
        engine=engine_name,
        session_id=f"benchmark-{engine_name}-{uuid4().hex}",
        user_id="benchmark",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def _database_size(path: str) -> int:
    candidate = Path(path)
    return candidate.stat().st_size if candidate.is_file() else 0


async def _wait_for_waiting(handle: Any) -> None:
    for _ in range(400):
        if handle.status == "waiting":
            return
        await asyncio.sleep(0.005)
    raise RuntimeError("resume benchmark did not reach a waiting task")


async def _resume_probe(
    engine_name: str,
    engine_type: type[Any],
    database_root: str,
) -> dict[str, Any]:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"ok": True})

    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    first_services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    first_services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    first_services.tools.protocols = first_services.protocols
    first_engine = engine_type()
    await first_engine.initialize(first_services)
    plan = compile_workflow(RESUME_WORKFLOW)
    handle = _handle(first_services, engine_name, plan)
    running = asyncio.create_task(first_engine.invoke(plan, handle, {}))
    await _wait_for_waiting(handle)
    running.cancel()
    try:
        await running
    except asyncio.CancelledError:
        pass
    finally:
        await first_engine.shutdown()
        first_services.close()

    restarted_services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    restarted_services.protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(handler))
    )
    restarted_services.tools.protocols = restarted_services.protocols
    restarted_engine = engine_type()
    await restarted_engine.initialize(restarted_services)
    persisted = restarted_services.invocations.get(handle.invocation_id)
    if persisted is None:
        raise RuntimeError("resume benchmark lost common invocation metadata")
    try:
        resumed = await restarted_engine.resume(persisted, {}, plan)
    finally:
        await restarted_engine.shutdown()
        restarted_services.close()
    return {
        "status": resumed.status,
        "side_effect_calls": len(calls),
        "unique_operation_ids": len(set(calls)),
        "operation_ids": calls,
    }


async def run_engine_benchmark(
    engine_name: str, *, iterations: int = 10, concurrency: int = 4
) -> dict[str, Any]:
    """Run adapter, throughput, checkpoint, and replay probes for one engine."""

    if iterations < 1 or concurrency < 1:
        raise ValueError("iterations and concurrency must be positive")
    engine_type, native_available = _load_engine(engine_name)
    plan = compile_workflow(BENCHMARK_WORKFLOW)

    with TemporaryDirectory(prefix=f"owa-{engine_name}-benchmark-") as database_root:
        config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
        services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
        engine = engine_type()
        await engine.initialize(services)
        native_database = services.engine_database_path(engine_name)
        before_checkpoint = _database_size(native_database)
        try:
            compile_started = time.perf_counter()
            for _ in range(iterations):
                await engine.compile(plan)
            compile_seconds = time.perf_counter() - compile_started

            async def invoke_once() -> Any:
                return await engine.invoke(
                    plan, _handle(services, engine_name, plan), {"input": "ok"}
                )

            sequential_started = time.perf_counter()
            sequential_results = [await invoke_once() for _ in range(iterations)]
            sequential_seconds = time.perf_counter() - sequential_started

            concurrent_started = time.perf_counter()
            concurrent_results = await asyncio.gather(*(invoke_once() for _ in range(concurrency)))
            concurrent_seconds = time.perf_counter() - concurrent_started
            after_checkpoint = _database_size(native_database)
        finally:
            await engine.shutdown()
            services.close()

        results = [*sequential_results, *concurrent_results]
        if any(result.status != "completed" for result in results):
            raise RuntimeError("engine benchmark invocation did not complete successfully")
        resume = await _resume_probe(engine_name, engine_type, database_root)

    return {
        "engine": engine_name,
        "native_available": native_available,
        "iterations": iterations,
        "concurrency": concurrency,
        "engine_compile_seconds_total": compile_seconds,
        "engine_compile_seconds_per_iteration": compile_seconds / iterations,
        "sequential_seconds_total": sequential_seconds,
        "sequential_seconds_per_invocation": sequential_seconds / iterations,
        "concurrent_seconds_total": concurrent_seconds,
        "concurrent_throughput_per_second": concurrency / max(concurrent_seconds, 1e-12),
        "native_checkpoint_bytes_before": before_checkpoint,
        "native_checkpoint_bytes_after": after_checkpoint,
        "native_checkpoint_bytes_delta": after_checkpoint - before_checkpoint,
        "interruption_resume": resume,
    }


async def run_benchmarks(
    *, engine_name: str = "all", iterations: int = 10, concurrency: int = 4
) -> dict[str, Any]:
    names = list(ENGINE_MODULES) if engine_name == "all" else [engine_name]
    return {
        "benchmark": "engine-adapters",
        "results": [
            await run_engine_benchmark(name, iterations=iterations, concurrency=concurrency)
            for name in names
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["all", *ENGINE_MODULES], default="all")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    result = asyncio.run(
        run_benchmarks(
            engine_name=args.engine,
            iterations=args.iterations,
            concurrency=args.concurrency,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
