from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job["steps"] if isinstance(step, dict)]


def test_every_image_platform_is_scanned_before_any_publication() -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    preflight = jobs["preflight"]
    image_platforms = {
        (entry["image"], entry["platform"]) for entry in preflight["strategy"]["matrix"]["include"]
    }
    assert image_platforms == {
        (image, platform)
        for image in (
            "adk",
            "langgraph",
            "sandbox-controller",
            "kubernetes-sandbox-controller",
        )
        for platform in ("linux/amd64", "linux/arm64")
    }
    scan_steps = [
        step
        for step in _steps(preflight)
        if step.get("uses") == "aquasecurity/trivy-action@v0.36.0"
    ]
    builds = [
        step for step in _steps(preflight) if step.get("uses") == "docker/build-push-action@v7"
    ]
    assert len(builds) == 1
    assert len(scan_steps) == 1
    assert builds[0]["with"]["load"] is True
    assert builds[0]["with"]["platforms"] == "${{ matrix.platform }}"
    assert scan_steps[0]["with"]["image-ref"] == builds[0]["with"]["tags"]
    assert scan_steps[0]["with"]["exit-code"] == "1"
    assert scan_steps[0]["with"]["severity"] == "CRITICAL,HIGH"
    assert not any(step.get("with", {}).get("push") is True for step in _steps(preflight))
    for job_name in ("publish", "publish-controllers"):
        job = jobs[job_name]
        assert "preflight" in job["needs"]
        assert "needs.preflight.result == 'success'" in job["if"]


def test_release_waits_for_exact_head_companions_triggered_by_main_push() -> None:
    workflows = ROOT / ".github" / "workflows"
    release: dict[str, Any] = yaml.safe_load(
        (workflows / "release.yml").read_text(encoding="utf-8")
    )
    gate = next(step for step in _steps(release["jobs"]["prepare"]) if step.get("id") == "gate")
    assert "python -m ci.release_gate" in gate["run"]
    assert '--sha "$RELEASE_SHA"' in gate["run"]
    for name in ("external-sandbox-ci.yml", "postgres-ci.yml"):
        companion: dict[str, Any] = yaml.load(
            (workflows / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        assert companion["on"]["push"]["branches"] == ["main"]
        assert "paths" not in companion["on"]["push"]


def test_formal_release_waits_for_runtime_and_controller_publication() -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    release = workflow["jobs"]["github-release"]
    assert set(release["needs"]) == {"prepare", "publish", "publish-controllers"}
    assert "needs.publish.result == 'success'" in release["if"]
    assert "needs.publish-controllers.result == 'success'" in release["if"]


def test_release_publishes_runtime_images_for_both_architectures() -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["publish"]

    assert any(step.get("uses") == "docker/setup-qemu-action@v4" for step in _steps(job))
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

    assert any(step.get("uses") == "docker/setup-qemu-action@v4" for step in _steps(job))
    published = [
        step["with"]
        for step in _steps(job)
        if step.get("uses") == "docker/build-push-action@v7"
        and step.get("with", {}).get("push") is True
    ]
    assert len(published) == 1
    assert set(published[0]["platforms"].split(",")) == {"linux/amd64", "linux/arm64"}


def test_docker_controller_removes_unused_cli_plugins_from_base_paths() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.sandbox-controller").read_text(encoding="utf-8")
    for plugin in ("docker-compose", "docker-buildx"):
        assert f"/usr/libexec/docker/cli-plugins/{plugin}" in dockerfile
        assert f"/usr/local/libexec/docker/cli-plugins/{plugin}" in dockerfile


def test_kubernetes_controller_removes_build_only_python_tooling() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.kubernetes-sandbox-controller").read_text(
        encoding="utf-8"
    )

    assert "python -m pip uninstall -y pip setuptools" in dockerfile
    assert "/usr/local/lib/python3.14/ensurepip" in dockerfile
