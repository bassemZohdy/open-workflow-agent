"""Release metadata is synthetic here; these tests publish or deploy nothing."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from ci.release_manifest import IMAGES, assemble, image_record, main

ROOT = Path(__file__).resolve().parents[2]


def records() -> list[dict[str, Any]]:
    return [
        dict(
            image_record(
                image,
                "a" * 40,
                "0.2.0",
                "sha256:" + f"{index:064x}",
                ["ghcr.io/test/" + image, "docker.io/test/" + image],
                "https://github.com/test/project/actions/runs/123",
            )
        )
        for index, image in enumerate(sorted(IMAGES))
    ]


def test_manifest_keeps_complete_immutable_set_and_original_inputs() -> None:
    original = records()
    before = copy.deepcopy(original)
    result = assemble(original)
    assert original == before
    assert result["source_commit"] == "a" * 40 and result["version"] == "0.2.0"
    assert {image["image"] for image in result["images"]} == IMAGES
    assert all(
        "@sha256:" in reference for image in result["images"] for reference in image["references"]
    )
    assert "no automatic checkpoint migration" in result["rollback_policy"]["persistence"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.pop(),
        lambda rows: rows.append(copy.deepcopy(rows[0])),
        lambda rows: rows[0].update(source_commit="b" * 40),
        lambda rows: rows[0].update(version="0.3.0"),
        lambda rows: rows[0].update(
            release_run_url="https://github.com/test/project/actions/runs/456"
        ),
        lambda rows: rows[0].update(platforms=["linux/amd64"]),
        lambda rows: rows[0].update(digest="sha256:" + "f" * 64),
        lambda rows: rows[0].update(format="foreign"),
        lambda rows: rows[0].update(extra="private"),
        lambda rows: rows[0].update(references=["ghcr.io/test/open-workflow-agent-adk:latest"]),
    ],
)
def test_missing_mixed_or_mutated_records_fail_closed(
    mutate: Callable[[list[dict[str, Any]]], None],
) -> None:
    values = records()
    mutate(values)
    before = copy.deepcopy(values)
    with pytest.raises(ValueError):
        assemble(values)
    assert values == before


@pytest.mark.parametrize(
    "digest,repository",
    [
        ("latest", "ghcr.io/test/open-workflow-agent-adk"),
        ("sha256:" + "a" * 64, "https://token:secret@example.test/image"),
        ("sha256:" + "a" * 64, "ghcr.io/test/open-workflow-agent-adk:latest"),
    ],
)
def test_mutable_or_credential_bearing_references_are_rejected(
    digest: str, repository: str
) -> None:
    with pytest.raises(ValueError):
        image_record(
            "open-workflow-agent-adk",
            "a" * 40,
            "0.2.0",
            digest,
            [repository, "docker.io/test/open-workflow-agent-adk"],
            "https://github.com/test/project/actions/runs/123",
        )


def test_cli_writes_valid_bundle_and_preserves_output_on_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "records"
    directory.mkdir()
    for record in records():
        (directory / (record["image"] + ".json")).write_text(json.dumps(record))
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["manifest", "assemble", "--directory", str(directory), "--output", str(output)],
    )
    assert main() == 0
    before = output.read_bytes()
    assert json.loads(before)["format"] == "owa/release-manifest/v1"
    next(directory.glob("*.json")).write_text('{"private":"must-not-disclose"}')
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 1 and output.read_bytes() == before
    assert "must-not-disclose" not in capsys.readouterr().err


def test_publication_requires_and_attaches_matched_manifest() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    jobs = workflow["jobs"]
    for name in ("publish", "publish-controllers"):
        steps = jobs[name]["steps"]
        record = next(step for step in steps if "ci.release_manifest image" in step.get("run", ""))
        assert record["env"]["DIGEST"] == "${{ steps.build.outputs.digest }}"
        assert record["env"]["SOURCE_SHA"] == "${{ github.event.workflow_run.head_sha }}"
        assert any(
            step.get("uses") == "actions/upload-artifact@v7"
            and step.get("with", {}).get("name", "").startswith("release-image-")
            for step in steps
        )
    manifest = jobs["release-record"]
    assert set(manifest["needs"]) == {"prepare", "publish", "publish-controllers"}
    assert any("ci.release_manifest assemble" in step.get("run", "") for step in manifest["steps"])
    release = jobs["github-release"]
    assert "needs.release-record.result == 'success'" in release["if"]
    assert any(
        'gh release create "$RELEASE_TAG" owa-release-manifest.json' in step.get("run", "")
        for step in release["steps"]
    )
