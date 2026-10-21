"""Calendar expectations are independent of scheduler delivery and SDK state."""

from datetime import UTC, datetime

import pytest
import yaml
from open_workflow_agent.errors import ExpressionError, ScheduleValidationError
from open_workflow_agent.schedule_profile import next_due, parse_schedule
from open_workflow_agent.workflow import compile_workflow


def instant(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


@pytest.mark.parametrize(
    "expression,base,zone,expected",
    [
        ("*/15 9-10 * * mon-fri", "2026-10-12T08:59:00+00:00", "UTC", "2026-10-12T09:00:00+00:00"),
        ("0 0 * * *", "2026-10-12T00:00:00+00:00", "UTC", "2026-10-13T00:00:00+00:00"),
        ("0 0 * * *", "2026-10-12T00:00:00+00:00", "Asia/Dubai", "2026-10-12T20:00:00+00:00"),
        ("0 0 * * *", "2026-03-28T23:00:00+00:00", "Europe/Berlin", "2026-03-29T22:00:00+00:00"),
        ("0 0 * * *", "2026-10-24T22:00:00+00:00", "Europe/Berlin", "2026-10-25T23:00:00+00:00"),
        # February has no 31st, but restricted weekday OR still makes this valid.
        ("0 0 31 2 tue", "2026-02-01T00:00:00+00:00", "UTC", "2026-02-03T00:00:00+00:00"),
    ],
)
def test_cron_calendar_timezone_and_dom_dow_or(expression, base, zone, expected):
    profile = parse_schedule({"cron": expression})
    assert next_due(profile, instant(base), zone) == instant(expected)


@pytest.mark.parametrize(
    "schedule",
    [
        {"cron": "0 0 31 2 *"},
        {"cron": "0 0 * * * *"},
        {"cron": "not a cron"},
        {"every": {"seconds": 0}},
        {"after": 1, "every": 2},
        {"cron": "0 0 * * *", "read": "data"},
        {"read": "raw"},
    ],
)
def test_invalid_schedule_rejected_before_calendar_dispatch(schedule):
    with pytest.raises(ScheduleValidationError):
        parse_schedule(schedule)


def test_schedule_on_uses_existing_admission_and_preserves_public_schema(tmp_path):
    source = {
        "document": {"dsl": "1.0.3", "namespace": "test", "name": "trigger", "version": "1.0.0"},
        "schedule": {"on": {"one": {"with": {"type": "ready"}}}, "read": "envelope"},
        "do": [{"report": {"set": {"done": True}}}],
    }
    plan = compile_workflow(source)
    assert plan.source["schedule"] == source["schedule"]
    # Unquoted OWS `on` must not be converted to a boolean by YAML1.1 rules.
    path = tmp_path / "trigger.yaml"
    path.write_text(
        yaml.safe_dump(source, sort_keys=False).replace("'on':", "on:"), encoding="utf-8"
    )
    yaml_plan = compile_workflow(path)
    assert yaml_plan.fingerprint == plan.fingerprint
    source["schedule"]["on"]["one"]["with"]["data"] = "${ $task.name }"
    with pytest.raises(ExpressionError):
        compile_workflow(source)


def test_after_and_every_calculate_relative_to_supplied_anchor():
    for kind in ("after", "every"):
        assert next_due(parse_schedule({kind: {"seconds": 2}}), 1000) == 1002
    assert datetime.fromtimestamp(1002, UTC).timestamp() == 1002
