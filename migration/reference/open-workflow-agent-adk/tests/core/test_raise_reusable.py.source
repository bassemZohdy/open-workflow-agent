"""Reusable `use.errors` references in raise tasks."""

import pytest

from openworkflow_adk import load, run_workflow
from openworkflow_adk.errors import OpenWorkflowError


def _document(tasks: list[dict], **extra: object):
    base: dict = {
        "document": {"dsl": "1.0.3", "namespace": "demo", "name": "raise", "version": "1.0.0"},
        "do": tasks,
    }
    base.update(extra)
    return load(base)


async def test_raise_resolves_reusable_error_reference() -> None:
    document = _document(
        [{"boom": {"raise": {"error": "notImplemented"}}}],
        use={
            "errors": {
                "notImplemented": {
                    "type": "https://demo.test/errors/not-implemented",
                    "status": 501,
                    "title": "Not Implemented",
                }
            }
        },
    )

    with pytest.raises(OpenWorkflowError) as raised:
        await run_workflow(document)

    assert raised.value.status == 501
    assert raised.value.type == "https://demo.test/errors/not-implemented"


async def test_raise_evaluates_expressions_against_workflow_definition() -> None:
    document = _document(
        [{"boom": {"raise": {"error": "notImplemented"}}}],
        use={
            "errors": {
                "notImplemented": {
                    "type": "https://demo.test/errors/not-implemented",
                    "status": 500,
                    "detail": (
                        "${ 'The workflow ' & $workflow.definition.document.name & ':' "
                        "& $workflow.definition.document.version & ' is incomplete' }"
                    ),
                }
            }
        },
    )

    with pytest.raises(OpenWorkflowError) as raised:
        await run_workflow(document)

    assert raised.value.detail == "The workflow raise:1.0.0 is incomplete"


async def test_raise_unknown_reference_is_rejected() -> None:
    document = _document([{"boom": {"raise": {"error": "missing"}}}])

    with pytest.raises(Exception):
        await run_workflow(document)
