"""Retain a matched immutable image set without publishing or deploying it."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

IMAGES = frozenset(
    {
        "open-workflow-agent-adk",
        "open-workflow-agent-langgraph",
        "open-workflow-agent-sandbox-controller",
        "open-workflow-agent-kubernetes-sandbox-controller",
    }
)
PLATFORMS = ["linux/amd64", "linux/arm64"]
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
_RUN = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*")


class ImageRecord(TypedDict):
    format: str
    image: str
    source_commit: str
    version: str
    digest: str
    platforms: list[str]
    references: list[str]
    release_run_url: str


def image_record(
    image: str, sha: str, version: str, digest: str, repositories: list[str], run_url: str
) -> ImageRecord:
    if image not in IMAGES or not _SHA.fullmatch(sha) or not _VERSION.fullmatch(version):
        raise ValueError("release image identity is invalid")
    if not _DIGEST.fullmatch(digest) or not _RUN.fullmatch(run_url):
        raise ValueError("release digest or run identity is invalid")
    if len(repositories) != 2 or len(set(repositories)) != 2:
        raise ValueError("both distinct publication registries are required")
    for repository in repositories:
        if not re.fullmatch(
            r"(?:ghcr\.io|docker\.io)/[a-z0-9][a-z0-9_.-]*/" + re.escape(image), repository
        ):
            raise ValueError("release image repository is invalid")
    if {repository.split("/", 1)[0] for repository in repositories} != {"ghcr.io", "docker.io"}:
        raise ValueError("both publication registries are required")
    return {
        "format": "owa/release-image/v1",
        "image": image,
        "source_commit": sha,
        "version": version,
        "digest": digest,
        "platforms": list(PLATFORMS),
        "references": sorted(f"{repository}@{digest}" for repository in repositories),
        "release_run_url": run_url,
    }


def validate_record(value: object) -> ImageRecord:
    if not isinstance(value, dict) or set(value) != set(ImageRecord.__annotations__):
        raise ValueError("release image record fields are invalid")
    scalar = ("image", "source_commit", "version", "digest", "release_run_url")
    if any(not isinstance(value[name], str) for name in scalar):
        raise ValueError("release image record types are invalid")
    references = value["references"]
    if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
        raise ValueError("release image references are invalid")
    digest = value["digest"]
    if any(not item.endswith("@" + digest) for item in references):
        raise ValueError("release image references do not match their digest")
    expected = image_record(
        value["image"],
        value["source_commit"],
        value["version"],
        digest,
        [item.rsplit("@", 1)[0] for item in references],
        value["release_run_url"],
    )
    if value != expected:
        raise ValueError("release image record is inconsistent")
    return expected


def assemble(records: Sequence[object]) -> dict[str, Any]:
    validated = [validate_record(record) for record in records]
    if len(validated) != len(IMAGES) or {record["image"] for record in validated} != IMAGES:
        raise ValueError("release manifest requires each runtime and controller exactly once")
    identities = {
        (record["source_commit"], record["version"], record["release_run_url"])
        for record in validated
    }
    if len(identities) != 1:
        raise ValueError("release manifest cannot mix commits, versions or publication runs")
    sha, version, run_url = identities.pop()
    return {
        "format": "owa/release-manifest/v1",
        "source_commit": sha,
        "version": version,
        "release_run_url": run_url,
        "images": sorted(validated, key=lambda record: record["image"]),
        "rollback_policy": {
            "selection": "use a previously qualified complete manifest, never rolling tags",
            "configuration": "retain the original compatible configuration and secret bindings",
            "persistence": (
                "retain compatible persistence and committed recovery evidence; "
                "no automatic checkpoint migration"
            ),
            "deployment": "operator-controlled; generating this record performs no deployment",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    image = commands.add_parser("image")
    for name in ("image", "sha", "version", "digest", "run-url"):
        image.add_argument("--" + name, required=True)
    image.add_argument("--repository", action="append", required=True)
    image.add_argument("--output", type=Path, required=True)
    combined = commands.add_parser("assemble")
    combined.add_argument("--directory", type=Path, required=True)
    combined.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "image":
            result: object = image_record(
                args.image, args.sha, args.version, args.digest, args.repository, args.run_url
            )
        else:
            files = sorted(args.directory.glob("*.json"))
            if len(files) != len(IMAGES) or any(path.stat().st_size > 1_048_576 for path in files):
                raise ValueError("release manifest input set is missing, oversized or duplicated")
            result = assemble([json.loads(path.read_text(encoding="utf-8")) for path in files])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (ValueError, OSError):
        parser.exit(1, "release manifest validation or storage failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
