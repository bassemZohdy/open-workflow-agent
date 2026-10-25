"""Guard matched S4-06 profile aggregation and hosted matrix wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks import matched
from benchmarks.aggregate_matched import aggregate_profiles
from benchmarks.matched import latency_summary, percentile, validate_concurrency

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "hosted,legacy,expected",
    [
        ("hosted-linux-7", "legacy-host", "hosted-linux-7"),
        (None, "explicit-benchmark-host", "explicit-benchmark-host"),
        (None, None, "local"),
        ("", "explicit-benchmark-host", "explicit-benchmark-host"),
        ("", "", "local"),
    ],
)
def test_report_identifies_the_actual_hosted_or_local_runner(
    monkeypatch, hosted: str | None, legacy: str | None, expected: str
) -> None:
    for variable, value in (("RUNNER_NAME", hosted), ("GITHUB_RUNNER", legacy)):
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    metadata = matched._metadata("adk", "sqlite", {"iterations": 30}, "workflow-fingerprint")
    assert metadata["runner"]["name"] == expected


def _profile(engine: str, backend: str, *, commit: str = "a" * 40) -> dict[str, Any]:
    return {
        "profile": "owa/s4-06-matched-performance/v1",
        "metadata": {
            "commit": commit,
            "engine": engine,
            "backend": backend,
            "workflow_fingerprint": "workflow-fingerprint",
            "parameters": {
                "iterations": 30,
                "warmups": 5,
                "concurrency": 8,
                "recovery_samples": 3,
            },
        },
        "measurements": {
            "concurrent_invocation_seconds": {
                "count": 8,
                "p50_seconds": 0.2,
                "p95_seconds": 0.3,
                "mean_seconds": 0.25,
            },
            "concurrent_wave_seconds": 2.0,
            "concurrent_throughput_per_second": 4.0,
        },
        "acceptance": {
            "all_invocations_completed": True,
            "recovery_replayed_side_effect_once": True,
            "fake_model_not_replayed": True,
        },
    }


def test_percentile_and_summary_use_deterministic_nearest_rank() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 50) == 2.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 95) == 4.0
    assert latency_summary([4.0, 1.0, 3.0, 2.0]) == {
        "count": 4,
        "p50_seconds": 2.0,
        "p95_seconds": 4.0,
        "mean_seconds": 2.5,
    }


@pytest.mark.parametrize("concurrency", [1, 8, 16])
def test_concurrency_validation_accepts_small_bounded_values(concurrency: int) -> None:
    validate_concurrency(concurrency)


@pytest.mark.parametrize("concurrency", [0, 17, True, 1.5])
def test_concurrency_validation_rejects_unbounded_or_noninteger_values(
    concurrency: Any,
) -> None:
    with pytest.raises(ValueError):
        validate_concurrency(concurrency)


@pytest.mark.parametrize(
    "profiles",
    [
        [_profile("adk", "sqlite")],
        [
            _profile("adk", "sqlite"),
            _profile("adk", "sqlite"),
            _profile("adk", "postgresql"),
            _profile("langgraph", "sqlite"),
            _profile("langgraph", "postgresql"),
        ],
        [
            _profile("adk", "sqlite"),
            _profile("adk", "postgresql"),
            _profile("langgraph", "sqlite"),
            _profile("langgraph", "postgresql", commit="b" * 40),
        ],
    ],
)
def test_aggregate_rejects_incomplete_duplicate_or_mixed_source_matrix(
    profiles: list[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError):
        aggregate_profiles(profiles)


def test_aggregate_requires_all_four_matched_engine_backend_cells() -> None:
    profiles = [
        _profile(engine, backend)
        for engine in ("adk", "langgraph")
        for backend in ("sqlite", "postgresql")
    ]

    report = aggregate_profiles(profiles)

    assert report["commit"] == "a" * 40
    assert len(report["profiles"]) == 4
    assert report["parameters"]["iterations"] == 30


def test_aggregate_rejects_profile_without_replay_assertions() -> None:
    profiles = [
        _profile(engine, backend)
        for engine in ("adk", "langgraph")
        for backend in ("sqlite", "postgresql")
    ]
    profiles[0]["acceptance"]["recovery_replayed_side_effect_once"] = False

    with pytest.raises(ValueError):
        aggregate_profiles(profiles)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda measurements, metadata: measurements.pop("concurrent_invocation_seconds"),
        lambda measurements, metadata: measurements.update(concurrent_wave_seconds=0),
        lambda measurements, metadata: measurements.update(concurrent_throughput_per_second=999),
        lambda measurements, metadata: metadata["parameters"].update(concurrency=17),
    ],
)
def test_aggregate_rejects_missing_or_unbounded_concurrent_measurement(
    mutation: Any,
) -> None:
    profiles = [
        _profile(engine, backend)
        for engine in ("adk", "langgraph")
        for backend in ("sqlite", "postgresql")
    ]
    mutation(profiles[0]["measurements"], profiles[0]["metadata"])

    with pytest.raises(ValueError):
        aggregate_profiles(profiles)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda summary: summary.pop("mean_seconds"),
        lambda summary: summary.update(p50_seconds=float("nan")),
        lambda summary: summary.update(mean_seconds=float("inf")),
        lambda summary: summary.update(p95_seconds=-0.1),
        lambda summary: summary.update(p50_seconds=0.4, p95_seconds=0.3),
    ],
)
def test_aggregate_rejects_malformed_concurrent_latency_summary(
    mutation: Any,
) -> None:
    profiles = [
        _profile(engine, backend)
        for engine in ("adk", "langgraph")
        for backend in ("sqlite", "postgresql")
    ]
    mutation(profiles[0]["measurements"]["concurrent_invocation_seconds"])

    with pytest.raises(ValueError):
        aggregate_profiles(profiles)


def test_postgresql_ci_runs_and_aggregates_the_complete_benchmark_matrix() -> None:
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "postgres-ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    paths = workflow["on"]["pull_request"]["paths"]
    assert "benchmarks/**" in paths
    assert "tests/ci/test_s4_06_benchmark.py" in paths
    jobs = workflow["jobs"]
    benchmark = jobs["s4-06-matched-benchmark"]
    cells = benchmark["strategy"]["matrix"]["include"]
    assert {cell["engine"] for cell in cells} == {"adk", "langgraph"}
    run_text = "\n".join(
        step["run"] for step in benchmark["steps"] if isinstance(step.get("run"), str)
    )
    assert "benchmarks/matched.py" in run_text
    assert "--extra ${{ matrix.extra }}" in run_text
    assert "--backend sqlite" in run_text
    assert "--backend postgresql" in run_text
    assert benchmark["strategy"]["max-parallel"] == "1"
    assert jobs["s4-06-benchmark-report"]["needs"] == ["s4-06-matched-benchmark"]
    report_run = "\n".join(
        step["run"]
        for step in jobs["s4-06-benchmark-report"]["steps"]
        if isinstance(step.get("run"), str)
    )
    assert "benchmarks/aggregate_matched.py" in report_run
    upload = next(
        step
        for step in jobs["s4-06-benchmark-report"]["steps"]
        if step.get("uses") == "actions/upload-artifact@v7"
    )
    assert upload["with"]["name"] == "s4-06-matched-performance-report"
