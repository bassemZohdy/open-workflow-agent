"""Self-contained recovery provenance hook, shared verbatim by all five packages."""

from __future__ import annotations

import hashlib
import json
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _digest(path: Path) -> str:
    # TOML text has the same authoritative content in LF and CRLF checkouts.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        metadata = root / "pyproject.toml"
        lock = root / "uv.lock"
        if not lock.is_file():
            raise RuntimeError("recovery provenance requires the package's committed lock")
        forced = build_data["force_include"]
        if self.target_name == "sdist":
            forced[str(lock)] = "uv.lock"
            forced[str(root / "hatch_build.py")] = "hatch_build.py"
            return
        if self.target_name != "wheel":
            return
        configuration = tomllib.loads(metadata.read_text(encoding="utf-8"))
        packages = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
        if len(packages) != 1:
            raise RuntimeError("recovery provenance requires exactly one package")
        package = Path(packages[0]).name
        self._provenance_directory = tempfile.TemporaryDirectory(prefix="owa-recovery-build-")
        record = Path(self._provenance_directory.name) / "_recovery_build.json"
        record.write_text(
            json.dumps(
                {
                    "profile": "owa/recovery-build/v1",
                    "project": configuration["project"]["name"],
                    "version": configuration["project"]["version"],
                    "lock_sha256": _digest(lock),
                    "metadata_sha256": _digest(metadata),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        forced[str(record)] = f"{package}/_recovery_build.json"

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if self.target_name == "wheel":
            self._provenance_directory.cleanup()
