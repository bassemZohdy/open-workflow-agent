from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job["steps"] if isinstance(step, dict)]


def test_release_publishes_runtime_images_for_both_architectures() -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["publish"]

    assert any(step.get("uses") == "docker/setup-qemu-action@v3" for step in _steps(job))
    published = [
        step["with"]
        for step in _steps(job)
        if step.get("uses") == "docker/build-push-action@v7"
        and step.get("with", {}).get("push") is True
    ]
    assert len(published) == 1
    assert set(published[0]["platforms"].split(",")) == {"linux/amd64", "linux/arm64"}


def test_release_publishes_controller_images_for_both_architectures() -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["publish-controllers"]

    assert any(step.get("uses") == "docker/setup-qemu-action@v3" for step in _steps(job))
    published = [
        step["with"]
        for step in _steps(job)
        if step.get("uses") == "docker/build-push-action@v7"
        and step.get("with", {}).get("push") is True
    ]
    assert len(published) == 1
    assert set(published[0]["platforms"].split(",")) == {"linux/amd64", "linux/arm64"}


def test_docker_controller_removes_unused_compose_plugin() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.sandbox-controller").read_text(encoding="utf-8")
    assert "rm -f /usr/local/libexec/docker/cli-plugins/docker-compose" in dockerfile
