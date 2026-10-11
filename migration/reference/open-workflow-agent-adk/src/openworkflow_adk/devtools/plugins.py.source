"""Discoverable, trust-checked plugin manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    entrypoint: str
    digest: str
    path: Path


class PluginRegistry:
    """Load plugin manifests only when their digest is trusted."""

    def __init__(self, trusted_digests: set[str] | frozenset[str] = frozenset()) -> None:
        self.trusted_digests = frozenset(trusted_digests)
        self.plugins: dict[str, PluginManifest] = {}

    def discover(self, directory: str | Path) -> list[PluginManifest]:
        discovered: list[PluginManifest] = []
        for path in sorted(Path(directory).glob("*/plugin.json")):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest not in self.trusted_digests:
                continue
            data: Any = json.loads(raw)
            manifest = PluginManifest(
                name=str(data["name"]),
                version=str(data["version"]),
                entrypoint=str(data["entrypoint"]),
                digest=digest,
                path=path,
            )
            self.plugins[manifest.name] = manifest
            discovered.append(manifest)
        return discovered
