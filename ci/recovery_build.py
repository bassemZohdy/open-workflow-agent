"""Validate immutable recovery metadata in a built wheel against its source inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify_wheel(wheel: Path, project: Path) -> dict[str, Any]:
    metadata = project / "pyproject.toml"
    configuration = tomllib.loads(metadata.read_text(encoding="utf-8"))
    packages = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    if len(packages) != 1:
        raise ValueError("recovery build has an unsupported package layout")
    record_path = f"{Path(packages[0]).name}/_recovery_build.json"
    expected = {
        "profile": "owa/recovery-build/v1",
        "project": configuration["project"]["name"],
        "version": configuration["project"]["version"],
        "lock_sha256": _digest(project / "uv.lock"),
        "metadata_sha256": _digest(metadata),
    }
    try:
        with zipfile.ZipFile(wheel) as archive:
            record = archive.getinfo(record_path)
            if record.file_size > 4096:
                raise ValueError("recovery build record is oversized")
            value = json.loads(archive.read(record))
            if value != expected:
                raise ValueError("recovery build record does not match package inputs")
    except (KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile):
        raise ValueError("wheel recovery build record is missing or invalid") from None
    return {**expected, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    wheels = sorted(args.dist.glob("*.whl"))
    if not wheels:
        raise SystemExit("no built wheels available for recovery validation")
    for wheel in wheels:
        print(json.dumps(verify_wheel(wheel, args.project), sort_keys=True))


if __name__ == "__main__":
    main()
