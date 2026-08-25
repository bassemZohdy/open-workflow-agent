"""Runtime datasource resolution shared by the framework-neutral services."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import ConfigurationError


def resolve_datasource(datasource: str | None) -> str | None:
    """Resolve a configured datasource to the local SQLite backend when supported.

    The common services intentionally share one SQLite file while using distinct
    tables. Engine adapters use the same resolved path for their own native
    tables. Network database URLs are rejected explicitly until a native,
    locked backend implementation is available for both engines.
    """

    if not datasource:
        return None
    if datasource == ":memory:":
        return datasource
    parsed = urlparse(datasource)
    if parsed.scheme and parsed.scheme != "sqlite":
        raise ConfigurationError(
            "configured persistence datasource is unsupported",
            details={"scheme": parsed.scheme, "supported": ["sqlite"]},
        )
    if parsed.scheme == "sqlite":
        raw_path = unquote(parsed.path)
        if os.name != "nt" and raw_path.startswith("//"):
            # A rooted path interpolated into sqlite:/// becomes sqlite:////
            # and urlparse retains both leading separators on POSIX.
            raw_path = raw_path[1:]
        if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2:
            if raw_path[2] == ":":
                raw_path = raw_path[1:]
        path = Path(raw_path or parsed.netloc)
        if not path:
            raise ConfigurationError("sqlite datasource must include a database path")
        return str(path)
    return datasource
