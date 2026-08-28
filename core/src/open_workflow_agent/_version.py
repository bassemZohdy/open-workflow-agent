"""Single runtime version source.

Kept in its own module so configuration, the HTTP application, and the A2A
Agent Card can import it without import cycles. CI verifies this value matches
the package `pyproject.toml` versions.
"""

from __future__ import annotations

__version__ = "0.1.0"
