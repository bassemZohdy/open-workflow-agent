from pathlib import Path

from openworkflow_adk import load

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_all_golden_fixtures_parse() -> None:
    fixtures = sorted(FIXTURES.glob("*.yaml"))

    assert len(fixtures) == 13
    for fixture in fixtures:
        assert load(fixture).document.namespace == "fixtures"
