"""Keep SDK driver diagnostics from rendering SQL parameters and checkpoints."""

from __future__ import annotations

import logging


class _StorageDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # aiosqlite logs repr(functools.partial(...)), including bound values
        # and serialized checkpoint blobs. Formatting first would expose them.
        record.msg = "native SQLite storage diagnostic (details withheld)"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def protect_native_storage_logging() -> None:
    logger = logging.getLogger("aiosqlite")
    if not any(isinstance(item, _StorageDiagnosticFilter) for item in logger.filters):
        logger.addFilter(_StorageDiagnosticFilter())
