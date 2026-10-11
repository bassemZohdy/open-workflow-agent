from pathlib import Path

from openworkflow_adk import load_example_gallery


def test_examples_gallery_is_complete() -> None:
    gallery = load_example_gallery(Path(__file__).parents[2] / "examples")

    assert {item["name"] for item in gallery} >= {"hello", "approval", "multi-agent"}
