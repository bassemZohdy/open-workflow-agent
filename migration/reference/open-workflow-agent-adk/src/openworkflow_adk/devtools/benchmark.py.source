"""Small deterministic workflow benchmark and regression guard."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from statistics import mean
from typing import Any

from openworkflow_adk.loader import load
from openworkflow_adk.runtime import run_workflow


def _document() -> Any:
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "benchmark",
                "name": "baseline",
                "version": "1.0.0",
            },
            "do": [
                {"seed": {"set": {"items": "[1,2,3,4,5]"}}},
                {
                    "parallel": {
                        "fork": {
                            "branches": [
                                {"left": {"set": {"left": '"done"'}}},
                                {"right": {"set": {"right": '"done"'}}},
                            ]
                        }
                    }
                },
                {
                    "fanout": {
                        "for": {"each": "item", "in": "$.items"},
                        "do": [{"step": {"set": {"last": "$.item"}}}],
                    }
                },
            ],
        }
    )


async def benchmark(iterations: int = 20) -> dict[str, float | int]:
    document = _document()
    samples: list[float] = []
    for index in range(iterations):
        started = time.perf_counter()
        await run_workflow(document, session_id=f"benchmark-{index}")
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)

    def percentile(value: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * value))]

    return {
        "iterations": iterations,
        "mean_ms": round(mean(samples), 3),
        "p95_ms": round(percentile(0.95), 3),
        "p99_ms": round(percentile(0.99), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--max-p99-ms", type=float)
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.iterations))
    print(json.dumps(result, indent=2))
    if args.max_p99_ms is not None and result["p99_ms"] > args.max_p99_ms:
        raise SystemExit(f"p99 latency exceeded {args.max_p99_ms}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
