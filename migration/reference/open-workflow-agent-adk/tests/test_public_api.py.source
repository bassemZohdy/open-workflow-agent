import importlib

import openworkflow_adk
import openworkflow_adk.internal


def test_all_root_exports_are_importable() -> None:
    """Every name advertised in openworkflow_adk.__all__ can be imported."""
    for name in openworkflow_adk.__all__:
        assert hasattr(openworkflow_adk, name), f"{name!r} missing from openworkflow_adk"


def test_all_internal_exports_are_importable() -> None:
    """Every name advertised in openworkflow_adk.internal.__all__ can be imported."""
    for name in openworkflow_adk.internal.__all__:
        assert hasattr(openworkflow_adk.internal, name), (
            f"{name!r} missing from openworkflow_adk.internal"
        )


def test_internal_module_is_stable() -> None:
    """The internal namespace can be imported directly."""
    assert importlib.import_module("openworkflow_adk.internal") is openworkflow_adk.internal
