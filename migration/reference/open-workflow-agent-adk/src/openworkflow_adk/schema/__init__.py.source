"""Access to the vendored OpenWorkflow schema baseline."""

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.3"
SCHEMA_DIR = Path(__file__).parent / "vendor" / SCHEMA_VERSION
_DSL_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def compatible_schema_version(dsl_version: str) -> str:
    """Resolve a document version to an available compatible schema baseline.

    Patch releases are treated as additive-compatible with the latest vendored
    schema for the same major/minor line. Minor and major releases require a
    newly vendored schema and are rejected explicitly.
    """
    match = _DSL_VERSION.fullmatch(dsl_version)
    if match is None:
        raise ValueError(f"invalid DSL version {dsl_version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    base_major, base_minor, base_patch = (int(part) for part in SCHEMA_VERSION.split("."))
    if (major, minor) != (base_major, base_minor) or patch < base_patch:
        raise ValueError(
            f"unsupported DSL version {dsl_version!r}; supported compatibility line is "
            f"{SCHEMA_VERSION}.x"
        )
    return SCHEMA_VERSION


def load_schema(format: str = "json") -> dict[str, Any]:
    """Load a vendored schema as a dictionary."""
    if format not in {"json", "yaml"}:
        raise ValueError("format must be 'json' or 'yaml'")
    path = SCHEMA_DIR / f"workflow.{format}"
    if not path.is_file():
        raise FileNotFoundError(f"vendored schema not found: {path}")
    if format == "json":
        return json.loads(path.read_text())

    import yaml

    return yaml.safe_load(path.read_text())


def load_schema_for(dsl_version: str, format: str = "json") -> dict[str, Any]:
    """Load the schema baseline compatible with a document DSL version."""
    compatible_schema_version(dsl_version)
    return load_schema(format)


def spec_drift_check() -> int:
    """Report the local schema baseline for use by CI and developers."""
    print(f"OpenWorkflow schema baseline: v{SCHEMA_VERSION}")
    print(f"YAML: {SCHEMA_DIR / 'workflow.yaml'}")
    print(f"JSON: {SCHEMA_DIR / 'workflow.json'}")
    return 0


__all__ = [
    "SCHEMA_VERSION",
    "compatible_schema_version",
    "load_schema",
    "load_schema_for",
    "spec_drift_check",
]
