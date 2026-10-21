"""Optional import smoke for OWA's text-only FastEmbed/Pillow profile."""

from __future__ import annotations

import pytest


def test_fastembed_text_embedding_imports_with_patched_pillow() -> None:
    pillow = pytest.importorskip("PIL")
    fastembed = pytest.importorskip("fastembed")

    assert pillow.__version__.startswith("13.")
    assert fastembed.TextEmbedding is not None
