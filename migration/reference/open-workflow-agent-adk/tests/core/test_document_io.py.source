"""Document-level `input.from` and `output.as` filters."""

from openworkflow_adk import load, run_workflow


async def test_document_input_from_transforms_run_input() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "filtered",
                "version": "1.0.0",
            },
            "input": {"from": "${ .envelope.payload }"},
            "do": [{"echo": {"set": {"question": "${ .question }"}}}],
        }
    )

    events = await run_workflow(document, {"envelope": {"payload": {"question": "hi"}}, "extra": 1})

    deltas = {
        k: v
        for event in events
        if event.actions
        for k, v in (event.actions.state_delta or {}).items()
    }
    assert deltas.get("question") == "hi"


async def test_document_output_as_shapes_final_output() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "shaped", "version": "1.0.0"},
            "output": {"as": "${ { 'answer': answer } }"},
            "do": [{"compute": {"set": {"answer": '"42"', "scratch": "'internal'"}}}],
        }
    )

    events = await run_workflow(document)

    assert events[-1].output == {"answer": "42"}
