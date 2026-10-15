"""Task-local action controls, kept outside managed catalog metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .effects import EffectIdentity
from .persistence import ExecutionHandle


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionActionContext:
    handle: ExecutionHandle
    identity: EffectIdentity
    observer: Callable[..., Any] | None = None


_ACTION: ContextVar[ExecutionActionContext | None] = ContextVar(
    "owa_execution_action", default=None
)


def current_action_context() -> ExecutionActionContext | None:
    return _ACTION.get()


@contextmanager
def action_scope(context: ExecutionActionContext) -> Iterator[None]:
    token = _ACTION.set(context)
    try:
        yield
    finally:
        _ACTION.reset(token)
