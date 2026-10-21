"""Bounded common observer for original protected-approval expiry decisions."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING

from .errors import OwaError

if TYPE_CHECKING:
    from .approvals import ApprovalService

_LOGGER = logging.getLogger(__name__)
_SAFE_ERROR_CODES = frozenset(
    {
        "approval_authorization_error",
        "approval_conflict",
        "approval_validation_error",
        "configuration_error",
        "workflow_execution_error",
    }
)


class ApprovalExpiryDispatcher:
    """Observe stored absolute expiries; never own or resume an invocation.

    The approval service owns the atomic first-terminal decision, scoped inbox
    and audit proofs. Stopping this observer leaves those immutable records and
    original clocks intact. Another runtime may observe the same due request.
    """

    def __init__(self, service: ApprovalService, *, poll_seconds: float, scan_limit: int) -> None:
        if (
            type(poll_seconds) not in {int, float}
            or not math.isfinite(poll_seconds)
            or not 0 < poll_seconds <= 60
        ):
            raise ValueError("approval expiry polling must be finite and positive")
        if type(scan_limit) is not int or not 1 <= scan_limit <= 1000:
            raise ValueError("approval expiry scans must be bounded")
        self.service = service
        self.poll_seconds = poll_seconds
        self.scan_limit = scan_limit
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self._last_error: str | None = None

    async def tick(self) -> int:
        if self._stopped or self.service.protected_store is None:
            return 0
        return await self.service.expire_due(limit=self.scan_limit)

    async def _run(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Store/API errors may contain policy or request data. Only a
                # fixed common code crosses this background diagnostic boundary.
                code = (
                    error.code
                    if isinstance(error, OwaError) and error.code in _SAFE_ERROR_CODES
                    else "approval_expiry_failed"
                )
                if code != self._last_error:
                    _LOGGER.warning("protected approval expiry observation failed (%s)", code)
                self._last_error = code
            await asyncio.sleep(self.poll_seconds)

    async def start(self) -> None:
        if self.service.protected_store is None:
            return
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name="owa-protected-approval-expiry")

    async def stop(self) -> None:
        self._stopped = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def close(self) -> None:
        """Fence future observations before synchronous service stores close."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
