from openworkflow_adk import OpenWorkflowError


def test_structured_workflow_error_serializes_without_empty_fields() -> None:
    error = OpenWorkflowError(type="https://example.test/failure", status=422, detail="bad input")

    assert error.as_dict() == {
        "type": "https://example.test/failure",
        "status": 422,
        "detail": "bad input",
    }
