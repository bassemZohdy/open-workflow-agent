"""Discoverable workflow example gallery helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_example_gallery(root: str | Path) -> list[dict[str, Any]]:
    """Load and validate an examples gallery rooted at ``root``."""
    directory = Path(root)
    gallery = json.loads((directory / "gallery.json").read_text())
    if not isinstance(gallery, list):
        raise ValueError("example gallery must be an array")
    for item in gallery:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ValueError("gallery entries require a file")
        if not (directory / item["file"]).is_file():
            raise FileNotFoundError(directory / item["file"])
    return gallery
