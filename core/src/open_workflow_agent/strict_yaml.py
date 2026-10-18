"""Strict YAML loading for Open Workflow documents.

PyYAML's safe loader silently keeps the last value of a duplicated mapping key,
which can change workflow behavior without any diagnostic. Workflow and catalog
definitions are therefore parsed with a loader that rejects duplicate keys at
every nesting level.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.constructor import ConstructorError


class DuplicateKeyError(ConstructorError):
    """Raised when a YAML mapping repeats a key."""


class _StrictSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:
                # Unhashable keys are rejected by the base constructor below.
                continue
            if duplicate:
                raise DuplicateKeyError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_strict_yaml(text: str) -> Any:
    """Parse ``text`` as safe YAML, raising ``yaml.YAMLError`` on duplicate keys."""

    return yaml.load(text, Loader=_StrictSafeLoader)  # noqa: S506 - SafeLoader subclass
