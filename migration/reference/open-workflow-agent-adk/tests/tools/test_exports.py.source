import ast

from openworkflow_adk import export_temporal, load


def test_temporal_export_contains_workflow_and_tasks() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "invoice-flow",
                "version": "1.0.0",
            },
            "do": [
                {"fetch": {"set": {"loaded": '"yes"'}}},
                {"notify": {"set": {"sent": '"yes"'}}},
            ],
        }
    )

    source = export_temporal(document)

    assert "class InvoiceFlowWorkflow" in source
    assert "execute_activity" in source
    assert "fetch" in source and "notify" in source


def test_temporal_export_sanitizes_empty_numeric_and_keyword_names() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "123",
                "version": "1.0.0",
            },
            "do": [
                {"123": {"set": {"value": "1"}}},
                {"class": {"set": {"value": "2"}}},
                {"!!!": {"set": {"value": "3"}}},
            ],
        }
    )

    source = export_temporal(document)

    ast.parse(source)
    assert "class Workflow_123Workflow" in source
    assert "activity_123" in source
    assert "activity_class" in source
    assert "state['activity']" in source
