"""Validate and combine the four S4-06 engine/backend JSON profiles."""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPECTED_CELLS = {
    ("adk", "sqlite"),
    ("adk", "postgresql"),
    ("langgraph", "sqlite"),
    ("langgraph", "postgresql"),
}
EXPECTED_ACCEPTANCE = {
    "all_invocations_completed",
    "recovery_replayed_side_effect_once",
    "fake_model_not_replayed",
}
EXPECTED_LATENCY_SUMMARY = {"count", "p50_seconds", "p95_seconds", "mean_seconds"}


def aggregate_profiles(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Require a complete, same-source parameter matrix before publishing."""

    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for profile in profiles:
        if profile.get("profile") != "owa/s4-06-matched-performance/v1":
            raise ValueError("unsupported benchmark profile version")
        metadata = profile.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("benchmark metadata is missing")
        cell = (metadata.get("engine"), metadata.get("backend"))
        if cell not in EXPECTED_CELLS or cell in indexed:
            raise ValueError("benchmark matrix contains an invalid or duplicate cell")
        indexed[cell] = profile
    if set(indexed) != EXPECTED_CELLS:
        raise ValueError("benchmark matrix must include both engines and both backends")

    commits = {profile["metadata"].get("commit") for profile in indexed.values()}
    parameter_encodings = {
        json.dumps(profile["metadata"].get("parameters"), sort_keys=True)
        for profile in indexed.values()
    }
    fingerprints = {profile["metadata"].get("workflow_fingerprint") for profile in indexed.values()}
    if (
        len(commits) != 1
        or "unavailable" in commits
        or len(parameter_encodings) != 1
        or len(fingerprints) != 1
        or None in fingerprints
    ):
        raise ValueError("benchmark matrix source, workflow or parameters do not match")
    for profile in indexed.values():
        acceptance = profile.get("acceptance")
        if (
            not isinstance(acceptance, dict)
            or set(acceptance) != EXPECTED_ACCEPTANCE
            or not all(acceptance.values())
        ):
            raise ValueError("a benchmark profile failed or omitted correctness assertions")
        measurements = profile.get("measurements")
        profile_parameters = profile["metadata"].get("parameters")
        concurrency = (
            profile_parameters.get("concurrency") if isinstance(profile_parameters, dict) else None
        )
        concurrent = (
            measurements.get("concurrent_invocation_seconds")
            if isinstance(measurements, dict)
            else None
        )
        wave_seconds = (
            measurements.get("concurrent_wave_seconds") if isinstance(measurements, dict) else None
        )
        throughput = (
            measurements.get("concurrent_throughput_per_second")
            if isinstance(measurements, dict)
            else None
        )
        latency_fields_valid = (
            isinstance(concurrent, dict)
            and set(concurrent) == EXPECTED_LATENCY_SUMMARY
            and isinstance(concurrent.get("count"), int)
            and not isinstance(concurrent.get("count"), bool)
            and concurrent.get("count") == concurrency
        )
        latency_values = (
            [
                concurrent.get("p50_seconds"),
                concurrent.get("p95_seconds"),
                concurrent.get("mean_seconds"),
            ]
            if isinstance(concurrent, dict)
            else []
        )
        latency_values_valid = all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
            for value in latency_values
        )
        if (
            not isinstance(concurrency, int)
            or isinstance(concurrency, bool)
            or not 1 <= concurrency <= 16
            or not latency_fields_valid
            or not latency_values_valid
            or concurrent["p50_seconds"] > concurrent["p95_seconds"]
            or not isinstance(wave_seconds, (int, float))
            or isinstance(wave_seconds, bool)
            or not math.isfinite(wave_seconds)
            or wave_seconds <= 0
            or not isinstance(throughput, (int, float))
            or isinstance(throughput, bool)
            or not math.isfinite(throughput)
            or throughput <= 0
            or not math.isclose(throughput, concurrency / wave_seconds, rel_tol=1e-9)
        ):
            raise ValueError("a benchmark profile omitted valid bounded concurrent measurements")

    return {
        "report": "owa/s4-06-matched-performance-report/v1",
        "commit": next(iter(commits)),
        "parameters": json.loads(next(iter(parameter_encodings))),
        "workflow_fingerprint": next(iter(fingerprints)),
        "interpretation": (
            "Matched workload measurements on named GitHub-hosted runners; compare only "
            "within this report and do not treat them as portable performance claims."
        ),
        "profiles": [indexed[cell] for cell in sorted(EXPECTED_CELLS)],
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profiles = [json.loads(path.read_text(encoding="utf-8")) for path in args.inputs]
    rendered = json.dumps(aggregate_profiles(profiles), indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
