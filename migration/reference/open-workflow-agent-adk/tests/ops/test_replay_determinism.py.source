from hypothesis import given
from hypothesis import strategies as st

from openworkflow_adk import load, replay_event_log, verify_replay_determinism


@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "state_delta": st.dictionaries(st.text(min_size=1, max_size=8), st.integers()),
                "output": st.one_of(st.none(), st.integers()),
                "error": st.none(),
            }
        ),
        max_size=10,
    )
)
def test_event_log_replay_reconstructs_state_and_last_output(events) -> None:
    state, output = replay_event_log(events, {"initial": 1})
    expected = {"initial": 1}
    expected_output = None
    for event in events:
        expected.update(event["state_delta"])
        if event["output"] is not None:
            expected_output = event["output"]
    assert (state, output) == (expected, expected_output)


async def test_deterministic_replay_matches_event_logs() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "deterministic",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": '"stable"'}}}],
        }
    )

    assert await verify_replay_determinism(document) is True
