"""Small, dependency-free scheduler for workflow trigger metadata."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from openworkflow_adk.durations import duration_seconds
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.resources.broker import Broker


def _cron_matches(expression: str, instant: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("workflow cron expressions must contain five fields")
    # Cron numbers Sunday as 0; datetime numbers Monday as 0.
    values = (instant.minute, instant.hour, instant.day, instant.month, (instant.weekday() + 1) % 7)
    for field, value in zip(fields, values):
        if field == "*":
            continue
        allowed = {int(part) for part in field.split(",")}
        if value not in allowed:
            return False
    return True


async def trigger_events(
    document: OpenWorkflowDocument, broker: Broker | None = None
) -> AsyncIterator[None]:
    """Yield once for each configured schedule trigger."""
    schedule = document.schedule or {}
    if "on" in schedule:
        if broker is None:
            raise ValueError("schedule.on requires a broker")
        while True:
            await broker.consume()
            yield None
    elif "after" in schedule:
        await asyncio.sleep(duration_seconds(schedule["after"]))
        yield None
    elif "every" in schedule:
        delay = duration_seconds(schedule["every"])
        while True:
            await asyncio.sleep(delay)
            yield None
    elif "cron" in schedule:
        expression = str(schedule["cron"])
        while True:
            now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            if _cron_matches(expression, now):
                yield None
                await asyncio.sleep(60)
            else:
                await asyncio.sleep(
                    max(
                        0.1,
                        (now + timedelta(minutes=1) - datetime.now(timezone.utc)).total_seconds(),
                    )
                )
    else:
        raise ValueError("workflow schedule must define every, cron, after, or on")
