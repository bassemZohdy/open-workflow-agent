"""Validate and combine the four S4-06 engine/backend JSON profiles."""

from __future__ import annotations

import argparse
import json
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
    parameters = {
        json.dumps(profile["metadata"].get("parameters"), sort_keys=True)
        for profile in indexed.values()
    }
    fingerprints = {
        profile["metadata"].get("workflow_fingerprint") for profile in indexed.values()
    }
    if (
        len(commits) != 1
        or "unavailable" in commits
        or len(parameters) != 1
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

    return {
        "report": "owa/s4-06-matched-performance-report/v1",
        "commit": next(iter(commits)),
        "parameters": json.loads(next(iter(parameters))),
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
