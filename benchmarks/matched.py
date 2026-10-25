"""Run one reproducible engine/backend performance profile for S4-06.

The profile is measurement evidence on a named runner, not a portable ranking.
It deliberately uses a deterministic fake model and a mocked HTTP receiver.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from uuid import uuid4

import httpx
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowPlan, compile_workflow

try:
    import resource
except ImportError:  # pragma: no cover - supported measurement host is Linux CI
    resource = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
ENGINE_MODULES = {
    "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
    "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
}
ENGINE_EXTRAS = {"adk": "native", "langgraph": "sqlite"}
BACKENDS = ("sqlite", "postgresql")
MAX_CONCURRENT_INVOCATIONS = 16
WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "benchmarks",
        "name": "s4-06-matched-profile",
        "version": "1.0.0",
    },
    "do": [
        {
            "infer": {
                "call": "llm:1.0.0@default",
                "with": {"prompt": "Return a short deterministic benchmark response."},
            }
        },
        {"finish": {"set": {"complete": True}}},
    ],
}
RECOVERY_WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "benchmarks",
        "name": "s4-06-recovery-profile",
        "version": "1.0.0",
    },
    "do": [
        {
            "infer": {
                "call": "llm:1.0.0@default",
                "with": {"prompt": "Return a short deterministic benchmark response."},
            }
        },
        {
            "side_effect": {
                "call": "http",
                "with": {
                    "method": "POST",
                    "endpoint": "https://benchmark.test/side-effect",
                    "body": {"action": "record"},
                },
            }
        },
        {"pause": {"wait": {"milliseconds": 60000}}},
        {"finish": {"set": {"complete": True}}},
    ],
}


def percentile(values: list[float], percentile_value: float) -> float:
    """Return a deterministic nearest-rank percentile from a nonempty sample."""

    if not values or not 0 < percentile_value <= 100:
        raise ValueError("percentile requires samples and a percentile in (0, 100]")
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile_value / 100) * len(ordered)))
    return ordered[rank - 1]


def latency_summary(samples_seconds: list[float]) -> dict[str, float | int]:
    return {
        "count": len(samples_seconds),
        "p50_seconds": percentile(samples_seconds, 50),
        "p95_seconds": percentile(samples_seconds, 95),
        "mean_seconds": sum(samples_seconds) / len(samples_seconds),
    }


def validate_concurrency(concurrency: int) -> None:
    """Keep hosted concurrency probes bounded to a small fixed workload."""

    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or not 1 <= concurrency <= MAX_CONCURRENT_INVOCATIONS
    ):
        raise ValueError("profile concurrency must be an integer in [1, 16]")


def _engine_type(engine_name: str) -> type[Any]:
    import importlib

    module_name, class_name = ENGINE_MODULES[engine_name]
    module = importlib.import_module(module_name)
    return cast(type[Any], getattr(module, class_name))


def _commit() -> str:
    configured = os.getenv("GITHUB_SHA")
    if configured:
        return configured
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _metadata(
    engine_name: str,
    backend: str,
    parameters: dict[str, int],
    workflow_fingerprint: str,
) -> dict[str, Any]:
    lock_path = ROOT / "engines" / engine_name / "uv.lock"
    lock_digest = (
        hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.is_file() else None
    )
    return {
        "commit": _commit(),
        "python": sys.version.split()[0],
        "engine_lock_sha256": lock_digest,
        "engine": engine_name,
        "selected_extras": [ENGINE_EXTRAS[engine_name], "knowledge", "postgres"],
        "backend": backend,
        "workflow_fingerprint": workflow_fingerprint,
        "runner": {
            "name": os.getenv("RUNNER_NAME") or os.getenv("GITHUB_RUNNER") or "local",
            "os": os.getenv("RUNNER_OS", platform.system()),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
        },
        "parameters": parameters,
        "provider": "deterministic_fake_model",
        "memory_metric": "process_lifetime_peak_rss_bytes (Linux ru_maxrss)",
        "measured_at_utc": datetime.now(UTC).isoformat(),
    }


def _require_linux_rss() -> None:
    if sys.platform != "linux":
        raise RuntimeError("matched S4-06 profile requires Linux ru_maxrss semantics")


def _peak_rss_bytes() -> int:
    _require_linux_rss()
    if sys.platform != "linux" or resource is None:
        raise RuntimeError("process peak RSS measurement is unavailable on this platform")
    # Linux reports ru_maxrss in KiB; this profile is hosted on Linux runners.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)


def _database_bytes(backend: str, database_root: str, datasource: str | None) -> int:
    if backend == "sqlite":
        return sum(path.stat().st_size for path in Path(database_root).rglob("*") if path.is_file())
    if datasource is None:
        raise RuntimeError("PostgreSQL datasource is required")
    import psycopg

    # PostgreSQL profiles get one fresh service/database per engine job; this
    # reports whole-database size, including all benchmark schemas and rows.
    with psycopg.connect(datasource) as connection:
        row = connection.execute("SELECT pg_database_size(current_database())").fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL persistence measurement is unavailable")
        return int(row[0])


def _config(backend: str, datasource: str | None) -> RuntimeConfig:
    persistence: dict[str, str] = {}
    if backend == "postgresql":
        if datasource is None:
            raise RuntimeError("OWA_TEST_POSTGRES_URL is required for PostgreSQL profile")
        persistence["datasource"] = datasource
    return RuntimeConfig.model_validate({"model": {"provider": "fake"}, "persistence": persistence})


def _services(
    config: RuntimeConfig,
    model: FakeModel,
    backend: str,
    database_root: str,
    *,
    receiver: Any | None = None,
) -> RuntimeServices:
    services = RuntimeServices(
        config,
        model=model,
        database_root=database_root if backend == "sqlite" else None,
    )
    if receiver is not None:
        services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
        services.tools.protocols = services.protocols
    return services


def _handle(services: RuntimeServices, engine_name: str, plan: WorkflowPlan) -> ExecutionHandle:
    return services.invocations.create(
        engine=engine_name,
        session_id=f"s4-06-{uuid4().hex}",
        user_id="benchmark",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


async def _timed_invoke(
    engine: Any, services: RuntimeServices, engine_name: str, plan: WorkflowPlan
) -> tuple[float, float]:
    started = time.perf_counter()
    persistence_started = time.perf_counter()
    handle = _handle(services, engine_name, plan)
    persistence_seconds = time.perf_counter() - persistence_started
    result = await engine.invoke(plan, handle, {"input": "fixed"})
    elapsed = time.perf_counter() - started
    if result.status != "completed":
        raise RuntimeError("matched benchmark invocation did not complete")
    return elapsed, persistence_seconds


async def _recovery_sample(
    engine_name: str,
    engine_type: type[Any],
    config: RuntimeConfig,
    backend: str,
    database_root: str,
) -> dict[str, Any]:
    side_effect_ids: list[str] = []
    model_calls: list[Any] = []

    async def receiver(request: httpx.Request) -> httpx.Response:
        side_effect_ids.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"accepted": True})

    plan = compile_workflow(RECOVERY_WORKFLOW)
    model = FakeModel()
    first_services = _services(config, model, backend, database_root, receiver=receiver)
    first_engine = engine_type()
    await first_engine.initialize(first_services)
    first_engine.executor._absolute_clock = lambda: 1000.0
    if first_engine._timer_dispatcher is not None:
        await first_engine._timer_dispatcher.stop()
    handle = _handle(first_services, engine_name, plan)
    suspend_started = time.perf_counter()
    waiting = await asyncio.wait_for(first_engine.invoke(plan, handle, {}), timeout=30)
    suspend_seconds = time.perf_counter() - suspend_started
    if waiting.status != "waiting" or handle._execution_lease is not None:
        raise RuntimeError("recovery profile did not persist a suspended invocation")

    restart_started = time.perf_counter()
    await first_engine.shutdown()
    first_services.close()

    resumed_services = _services(config, model, backend, database_root, receiver=receiver)
    resumed_engine = engine_type()
    await resumed_engine.initialize(resumed_services)
    resumed_engine.executor._absolute_clock = lambda: 1000.0
    if resumed_engine._timer_dispatcher is not None:
        await resumed_engine._timer_dispatcher.stop()
    persisted = resumed_services.invocations.get(handle.invocation_id)
    if persisted is None:
        raise RuntimeError("recovery profile lost invocation metadata after restart")
    resumed = await resumed_engine.resume(persisted, {}, plan)
    restart_resume_seconds = time.perf_counter() - restart_started
    await resumed_engine.shutdown()
    resumed_services.close()
    model_calls.extend(model.calls)
    if (
        resumed.status != "completed"
        or len(side_effect_ids) != 1
        or len(set(side_effect_ids)) != 1
        or len(model_calls) != 1
    ):
        raise RuntimeError("recovery replay changed fake-model or side-effect execution")
    return {
        "initial_suspend_seconds": suspend_seconds,
        "restart_resume_seconds": restart_resume_seconds,
        "status": resumed.status,
        "fake_model_calls": len(model_calls),
        "side_effect_calls": len(side_effect_ids),
        "unique_operation_ids": len(set(side_effect_ids)),
    }


async def run_profile(
    engine_name: str,
    backend: str,
    *,
    iterations: int = 30,
    warmups: int = 5,
    concurrency: int = 8,
    recovery_samples: int = 3,
) -> dict[str, Any]:
    """Run matched normal and restart/recovery measurements for one cell."""

    _require_linux_rss()
    if engine_name not in ENGINE_MODULES or backend not in BACKENDS:
        raise ValueError("unsupported engine or persistence backend")
    if iterations < 2 or warmups < 1 or recovery_samples < 2:
        raise ValueError("profile requires iterations>=2, warmups>=1, recovery_samples>=2")
    validate_concurrency(concurrency)
    parameters = {
        "iterations": iterations,
        "warmups": warmups,
        "concurrency": concurrency,
        "recovery_samples": recovery_samples,
    }
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    config = _config(backend, datasource)
    engine_type = _engine_type(engine_name)
    plan = compile_workflow(WORKFLOW)

    with TemporaryDirectory(prefix=f"owa-s4-06-{engine_name}-{backend}-") as database_root:
        bytes_before = _database_bytes(backend, database_root, datasource)
        model = FakeModel()
        services = _services(config, model, backend, database_root)
        engine = engine_type()
        await engine.initialize(services)
        compile_samples: list[float] = []
        for _ in range(warmups):
            await engine.compile(plan)
            await _timed_invoke(engine, services, engine_name, plan)
        for _ in range(iterations):
            started = time.perf_counter()
            await engine.compile(plan)
            compile_samples.append(time.perf_counter() - started)

        cpu_started = time.process_time()
        sequential_results = [
            await _timed_invoke(engine, services, engine_name, plan) for _ in range(iterations)
        ]
        sequential_samples = [result[0] for result in sequential_results]
        persistence_create_samples = [result[1] for result in sequential_results]

        async def timed_concurrent() -> tuple[float, float]:
            return await _timed_invoke(engine, services, engine_name, plan)

        concurrent_started = time.perf_counter()
        concurrent_samples = await asyncio.gather(*(timed_concurrent() for _ in range(concurrency)))
        concurrent_wall_seconds = time.perf_counter() - concurrent_started
        invocation_cpu_seconds = time.process_time() - cpu_started
        all_latency_samples = [*sequential_samples, *(item[0] for item in concurrent_samples)]
        persistence_create_samples.extend(item[1] for item in concurrent_samples)
        await engine.shutdown()
        services.close()

        recovery_config = config
        recovery_results: list[dict[str, Any]] = []
        for _ in range(recovery_samples):
            recovery_results.append(
                await _recovery_sample(
                    engine_name,
                    engine_type,
                    recovery_config,
                    backend,
                    database_root,
                )
            )
        bytes_after = _database_bytes(backend, database_root, datasource)
        peak_rss = _peak_rss_bytes()

    return {
        "profile": "owa/s4-06-matched-performance/v1",
        "metadata": _metadata(engine_name, backend, parameters, plan.fingerprint),
        "measurements": {
            "compile_seconds": latency_summary(compile_samples),
            "sequential_invocation_seconds": latency_summary(sequential_samples),
            "concurrent_invocation_seconds": latency_summary(
                [item[0] for item in concurrent_samples]
            ),
            "invocation_seconds": latency_summary(all_latency_samples),
            "invocation_record_create_seconds": latency_summary(persistence_create_samples),
            "concurrent_wave_seconds": concurrent_wall_seconds,
            "concurrent_throughput_per_second": concurrency / max(concurrent_wall_seconds, 1e-12),
            "invocation_cpu_seconds": invocation_cpu_seconds,
            "process_lifetime_peak_rss_bytes": peak_rss,
            "persistence_bytes_before": bytes_before,
            "persistence_bytes_after": bytes_after,
            "persistence_bytes_delta": max(0, bytes_after - bytes_before),
            "persistence_footprint_scope": (
                "temporary common and native SQLite files"
                if backend == "sqlite"
                else "whole PostgreSQL service database; fresh service per engine job"
            ),
            "restart_resume_seconds": latency_summary(
                [float(item["restart_resume_seconds"]) for item in recovery_results]
            ),
            "initial_suspend_seconds": latency_summary(
                [float(item["initial_suspend_seconds"]) for item in recovery_results]
            ),
            "recovery_samples": recovery_results,
        },
        "acceptance": {
            "all_invocations_completed": True,
            "recovery_replayed_side_effect_once": all(
                item["side_effect_calls"] == 1 and item["unique_operation_ids"] == 1
                for item in recovery_results
            ),
            "fake_model_not_replayed": all(
                item["fake_model_calls"] == 1 for item in recovery_results
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=tuple(ENGINE_MODULES), required=True)
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--recovery-samples", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        run_profile(
            args.engine,
            args.backend,
            iterations=args.iterations,
            warmups=args.warmups,
            concurrency=args.concurrency,
            recovery_samples=args.recovery_samples,
        )
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
