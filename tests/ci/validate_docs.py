"""Validate that relative Markdown links point at repository content."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")


def local_link_targets(path: Path) -> Iterator[str]:
    for match in MARKDOWN_LINK.finditer(path.read_text(encoding="utf-8")):
        target = match.group(1).strip()
        if target.startswith("<") and ">" in target:
            target = target[1 : target.index(">")]
        else:
            target = target.split(maxsplit=1)[0]
        parsed = urlsplit(target)
        if parsed.scheme or target.startswith("//") or target.startswith("#"):
            continue
        if parsed.path:
            yield unquote(parsed.path)


def main() -> int:
    errors: list[str] = []
    for path in sorted(REPOSITORY_ROOT.rglob("*.md")):
        if any(part in {".git", ".venv", "__pycache__", "tmp"} for part in path.parts):
            continue
        for target in local_link_targets(path):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                errors.append(f"{path.relative_to(REPOSITORY_ROOT)} -> {target}")
    if errors:
        print("Broken relative Markdown links:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Markdown relative-link validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
