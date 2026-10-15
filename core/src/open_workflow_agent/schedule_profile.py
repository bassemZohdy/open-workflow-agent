"""Private admitted scheduling semantics over the unchanged OWS object."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from .errors import ScheduleValidationError

ScheduleKind = Literal["after", "every", "cron", "on"]
ScheduleRead = Literal["data", "envelope", "raw"]


@dataclass(frozen=True, slots=True)
class ScheduleProfile:
    kind: ScheduleKind
    interval_seconds: float | None = None
    cron: str | None = None
    strategy: Mapping[str, Any] | None = None
    read: ScheduleRead = "data"


def parse_schedule(schedule: Any) -> ScheduleProfile:
    # Imported at invocation, after workflow module initialization. Duration
    # and event strategy admission remain shared with ordinary tasks.
    from .workflow import _duration_seconds, _validate_listen_capability

    if not isinstance(schedule, Mapping):
        raise ScheduleValidationError("workflow schedule must be an object")
    modes = [kind for kind in ("after", "every", "cron", "on") if kind in schedule]
    if len(modes) != 1 or set(schedule) - {"after", "every", "cron", "on", "read"}:
        raise ScheduleValidationError("schedule requires exactly one scheduling mode")
    if "read" in schedule and modes[0] != "on":
        raise ScheduleValidationError("schedule.read requires schedule.on")
    kind = cast(ScheduleKind, modes[0])
    if kind in {"after", "every"}:
        duration = _duration_seconds(schedule[kind])
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise ScheduleValidationError("schedule duration must be greater than zero")
        return ScheduleProfile(kind, interval_seconds=duration)
    if kind == "cron":
        expression = schedule[kind]
        if not isinstance(expression, str) or len(expression) > 256:
            raise ScheduleValidationError("schedule.cron must be a bounded expression")
        profile = ScheduleProfile(kind, cron=expression)
        # Calendar feasibility is checked with a bounded search, including
        # correct DOM/DOW OR behavior rather than a contradictory strict check.
        next_due(profile, datetime(2024, 1, 1, tzinfo=UTC).timestamp())
        return profile
    strategy = schedule["on"]
    read = schedule.get("read", "data")
    if not isinstance(read, str) or read not in {"data", "envelope", "raw"}:
        raise ScheduleValidationError("schedule.read is invalid")
    _validate_listen_capability({"to": strategy}, "/schedule/on")
    return ScheduleProfile(kind, strategy=copy.deepcopy(strategy), read=cast(ScheduleRead, read))


def next_due(profile: ScheduleProfile, after: float, timezone: str = "UTC") -> float:
    """Calculate the next nominal instant; never consult the host timezone."""
    if isinstance(after, bool) or not isinstance(after, (int, float)) or not math.isfinite(after):
        raise ScheduleValidationError("schedule clock must be finite")
    if profile.kind in {"every", "after"}:
        result = after + float(profile.interval_seconds or 0)
        if not math.isfinite(result) or result <= after:
            raise ScheduleValidationError("schedule next instant is invalid")
        return result
    if profile.kind != "cron" or profile.cron is None:
        raise ScheduleValidationError("event schedules have no calendar due instant")
    try:
        if len(profile.cron.split()) != 5:
            raise ValueError("five cron fields are required")
        module = import_module("croniter")
        parser = module.croniter
        base = datetime.fromtimestamp(after, ZoneInfo(timezone))
        fields = profile.cron.split()
        expressions = [profile.cron]
        if fields[2] != "*" and fields[4] != "*":
            # The pinned parser can reject a feasible OR schedule if the DOM
            # alternative has no date (February31 + Tuesday). Iterate each
            # standard calendar alternative independently, then take the first.
            dom = fields.copy()
            dom[4] = "*"
            dow = fields.copy()
            dow[2] = "*"
            expressions = [" ".join(dom), " ".join(dow)]
        candidates = []
        for expression in expressions:
            try:
                candidates.append(
                    float(
                        parser(
                            expression, base, day_or=True, max_years_between_matches=8
                        ).get_next()
                    )
                )
            except module.CroniterBadDateError:
                continue
        if not candidates:
            raise ValueError("cron has no bounded next occurrence")
        result = min(candidates)
        if not math.isfinite(result) or result <= after:
            raise ValueError("cron next instant must advance")
        return result
    except Exception:
        raise ScheduleValidationError("cron expression or deployment timezone is invalid") from None
