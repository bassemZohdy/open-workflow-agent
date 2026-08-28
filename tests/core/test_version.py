from __future__ import annotations

import tomllib
from pathlib import Path

import open_workflow_agent

RUNTIME_VERSION = open_workflow_agent.__version__


def test_runtime_version_matches_core_package() -> None:
    core_project = tomllib.loads(
        (Path(__file__).parents[2] / "core" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert RUNTIME_VERSION == core_project["project"]["version"]


def test_runtime_version_matches_root_package() -> None:
    root_project = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert RUNTIME_VERSION == root_project["project"]["version"]
