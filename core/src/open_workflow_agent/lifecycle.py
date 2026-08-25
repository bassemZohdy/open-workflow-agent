"""Common invocation lifecycle controls shared by both engine adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from .errors import InvocationCancelled

INVOCATION_STATES = ("running", "waiting", "completed", "faulted", "cancelled")
TERMINAL_INVOCATION_STATES = frozenset({"completed", "faulted", "cancelled"})
VALID_INVOCATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "running": frozenset({"running", "waiting", "completed", "faulted", "cancelled"}),
    "waiting": frozenset({"waiting", "running", "faulted", "cancelled"}),
    "completed": frozenset({"completed"}),
    "faulted": frozenset({"faulted"}),
    "cancelled": frozenset({"cancelled"}),
}


class CancellationToken:
    """Cooperative cancellation with interruptible sleeps and awaits."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "cancelled") -> bool:
        if self._event.is_set():
            return False
        self.reason = reason
        self._event.set()
        return True

    def checkpoint(self) -> None:
        if self.cancelled:
            raise InvocationCancelled(
                "invocation cancellation requested", details={"reason": self.reason or "cancelled"}
            )

    async def sleep(self, seconds: float) -> None:
        self.checkpoint()
        if seconds <= 0:
            await asyncio.sleep(0)
            self.checkpoint()
            return
        sleeper = asyncio.create_task(asyncio.sleep(seconds))
        cancellation = asyncio.create_task(self._event.wait())
        done, pending = await asyncio.wait(
            {sleeper, cancellation}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancellation in done and cancellation.result():
            sleeper.cancel()
            await asyncio.gather(sleeper, return_exceptions=True)
            self.checkpoint()

    async def wait_cancelled(self) -> None:
        await self._event.wait()

    async def await_operation(self, operation: Awaitable[Any]) -> Any:
        self.checkpoint()
        task = asyncio.ensure_future(operation)
        cancellation = asyncio.create_task(self._event.wait())
        done, pending = await asyncio.wait(
            {task, cancellation}, return_when=asyncio.FIRST_COMPLETED
        )
        for item in pending:
            item.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancellation in done and cancellation.result():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.checkpoint()
        return task.result()


@dataclass(slots=True)
class LifecycleControl:
    """In-process control for an active invocation; never persisted or exposed."""

    token: CancellationToken = field(default_factory=CancellationToken)
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)
    resume_input: Any = None

    def request_resume(self, value: Any) -> None:
        self.resume_input = value
        self.resume_event.set()

    async def wait_or_resume(self, seconds: float) -> bool:
        """Return true when externally resumed, false when the timer elapsed."""

        timer = asyncio.create_task(asyncio.sleep(max(0.0, seconds)))
        resumed = asyncio.create_task(self.resume_event.wait())
        cancelled = asyncio.create_task(self.token.wait_cancelled())
        done, pending = await asyncio.wait(
            {timer, resumed, cancelled}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancelled in done:
            self.token.checkpoint()
        return resumed in done and resumed.result()


@dataclass(slots=True)
class ActiveInvocation:
    control: LifecycleControl
    task: asyncio.Task[Any] | None
    result: asyncio.Future[Any]
